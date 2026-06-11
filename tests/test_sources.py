"""Release-source parsing + the refresh orchestrator (pytest-httpx mocked)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from homewatch import sources
from homewatch.sources import base
from homewatch.sources.apple_developer import parse_title
from homewatch.sources.apple_security import AppleSecuritySource, parse_security_name
from homewatch.sources.ha_release import URL as HA_CORE_URL
from homewatch.sources.homepod_notes import HomePodNotesSource

FIX = Path(__file__).parent / "fixtures"


# --- pure parsers --------------------------------------------------------------


def test_detect_channel():
    assert base.detect_channel("2026.4.3") == "stable"
    assert base.detect_channel("2026.5.0b1") == "beta"
    assert base.detect_channel("2026.5.0rc1") == "rc"


def test_clean_version_strips_v():
    assert base.clean_version("v14.2") == "14.2"
    assert base.clean_version("2026.4.3") == "2026.4.3"


def test_apple_developer_parse_title_with_build():
    rel = parse_title("iOS 18.4 (22E240)")
    assert rel.product == "ios"
    assert rel.version == "18.4"
    assert rel.notes == "build 22E240"
    assert rel.channel == "stable"


def test_apple_developer_parse_beta():
    rel = parse_title("iPadOS 18.5 beta 2 (22F5054b)")
    assert rel.product == "ipados"
    assert rel.version == "18.5"
    assert rel.channel == "beta"


def test_apple_developer_non_release_title():
    assert parse_title("Some unrelated announcement") is None


def test_security_name_multi_os():
    pairs = parse_security_name("iOS 18.4 and iPadOS 18.4")
    assert ("ios", "18.4") in pairs
    assert ("ipados", "18.4") in pairs


def test_security_name_skips_marketing_word():
    assert parse_security_name("macOS Sequoia 15.4") == [("macos", "15.4")]


def test_apple_security_parse_fixture():
    html = (FIX / "apple_security.html").read_text()
    releases = AppleSecuritySource().parse(html)
    products = {(r.product, r.version) for r in releases}
    assert ("ios", "18.4") in products
    assert ("ipados", "18.4") in products
    assert ("macos", "15.4") in products
    assert ("homepod_software", "18.4") in products
    hp = next(r for r in releases if r.product == "homepod_software")
    assert hp.released_at == "2026-04-15"


def test_homepod_notes_parse_fixture():
    html = (FIX / "homepod_notes.html").read_text()
    releases = HomePodNotesSource().parse(html)
    versions = {r.version for r in releases}
    assert {"18.4", "18.3"} <= versions
    v184 = next(r for r in releases if r.version == "18.4")
    # This page carries notes but not dates; date comes from apple_security.
    assert v184.released_at is None
    assert v184.notes


def test_homepod_notes_unparseable_sets_warning():
    from homewatch.models import SourceState

    state = SourceState(source="homepod_notes")
    out = HomePodNotesSource().parse("<html><body>nothing here</body></html>", state)
    assert out == []
    assert state.last_status.startswith("warning")


# --- orchestrator (mocked HTTP) ------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_single_source_idempotent(db, httpx_mock):
    atom = (FIX / "ha_core.atom").read_bytes()
    httpx_mock.add_response(url=HA_CORE_URL, content=atom)
    httpx_mock.add_response(url=HA_CORE_URL, content=atom)  # second refresh

    async with httpx.AsyncClient() as client:
        first = await sources.refresh(db, client, source="ha_core_atom")
        second = await sources.refresh(db, client, source="ha_core_atom")

    assert first["ha_core_atom"]["new"] == 2
    assert first["ha_core_atom"]["errors"] == []
    # Idempotent: nothing new on the second pass.
    assert second["ha_core_atom"]["new"] == 0
    assert second["ha_core_atom"]["seen"] == 2

    rows = base.list_releases(db, product="home_assistant_core")
    assert {r["version"] for r in rows} == {"2026.4.3", "2026.5.0b1"}
    assert {r["channel"] for r in rows} == {"stable", "beta"}


@pytest.mark.asyncio
async def test_refresh_captures_source_error(db, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, status_code=500)
    async with httpx.AsyncClient() as client:
        result = await sources.refresh(db, client, source="ha_core_atom")
    assert result["ha_core_atom"]["errors"]
    state = base.load_state(db, "ha_core_atom")
    assert state.last_status.startswith("error")


@pytest.mark.asyncio
async def test_apple_developer_404_tolerated(db, httpx_mock):
    from homewatch.sources.apple_developer import URL as DEV_URL

    httpx_mock.add_response(url=DEV_URL, status_code=404)
    async with httpx.AsyncClient() as client:
        result = await sources.refresh(db, client, source="apple_developer_rss")
    # 404 is not an error — feed is just unavailable.
    assert result["apple_developer_rss"]["errors"] == []
    assert result["apple_developer_rss"]["new"] == 0


def test_unknown_source_rejected():
    with pytest.raises(KeyError):
        sources.select_sources("nope")


def test_upsert_gap_fills_across_sources(db):
    from homewatch.models import Release

    # homepod_notes lands first: notes, no date.
    assert base.upsert_release(db, Release(
        product="homepod_software", version="18.4", channel="stable",
        source="homepod_notes", notes="Adds crossfade.")) is True
    # apple_security lands second: date, no notes. Not "new", but back-fills date.
    assert base.upsert_release(db, Release(
        product="homepod_software", version="18.4", channel="stable",
        source="apple_security", released_at="2026-04-15", title="HomePod Software 18.4",
    )) is False
    row = base.latest_release(db, "homepod_software")
    assert row["released_at"] == "2026-04-15"  # filled by apple_security
    assert row["notes"] == "Adds crossfade."   # preserved from homepod_notes


def test_latest_release(db):
    base.upsert_release(
        db,
        __import__("homewatch.models", fromlist=["Release"]).Release(
            product="homepod_software", version="18.3", source="x",
            channel="stable", released_at="2026-01-27",
        ),
    )
    base.upsert_release(
        db,
        __import__("homewatch.models", fromlist=["Release"]).Release(
            product="homepod_software", version="18.4", source="x",
            channel="stable", released_at="2026-04-15",
        ),
    )
    row = base.latest_release(db, "homepod_software")
    assert row["version"] == "18.4"
