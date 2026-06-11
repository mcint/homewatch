"""endoflife.date source: parsing, gap-fill of dates, per-product tolerance."""

from __future__ import annotations

import httpx
import pytest

from homewatch import sources
from homewatch.models import Release
from homewatch.sources import base
from homewatch.sources.endoflife import API, PRODUCTS, EndOfLifeSource

TVOS = [
    {"cycle": "18", "releaseDate": "2024-09-16", "latest": "18.6",
     "latestReleaseDate": "2025-07-29", "eol": False},
    {"cycle": "26", "releaseDate": "2025-09-15", "latest": "26.5",
     "latestReleaseDate": "2026-05-11", "eol": False},
]


def _mock_all(httpx_mock, **overrides):
    for slug in PRODUCTS:
        httpx_mock.add_response(url=API.format(slug=slug),
                                json=overrides.get(slug, []))


def test_cycles_to_releases_emits_dotzero_and_latest():
    rels = EndOfLifeSource()._cycles_to_releases("tvos", "tvos", TVOS)
    pairs = {(r.version, r.released_at) for r in rels}
    assert ("18.0", "2024-09-16") in pairs   # cycle .0
    assert ("18.6", "2025-07-29") in pairs   # latest patch
    assert ("26.0", "2025-09-15") in pairs


async def test_refresh_gap_fills_undated_tvos(db, httpx_mock):
    # A prior source discovered tvOS 18.6 with no date.
    base.upsert_release(db, Release(product="tvos", version="18.6",
                                    channel="stable", source="apple_security"))
    assert base.list_releases(db, product="tvos")[0]["released_at"] is None

    _mock_all(httpx_mock, tvos=TVOS)
    async with httpx.AsyncClient() as client:
        res = await sources.refresh(db, client, source="endoflife")
    assert res["endoflife"]["errors"] == []

    row = next(r for r in base.list_releases(db, product="tvos")
               if r["version"] == "18.6")
    assert row["released_at"] == "2025-07-29"  # back-filled from endoflife


async def test_refresh_tolerates_one_product_404(db, httpx_mock):
    for slug in PRODUCTS:
        if slug == "visionos":
            httpx_mock.add_response(url=API.format(slug=slug), status_code=404)
        else:
            httpx_mock.add_response(url=API.format(slug=slug),
                                    json=TVOS if slug == "tvos" else [])
    async with httpx.AsyncClient() as client:
        res = await sources.refresh(db, client, source="endoflife")
    # 404 on one product is not a hard error; the rest still load.
    assert res["endoflife"]["new"] > 0
    assert res["endoflife"]["errors"] == []
