"""Timeline merge/order/format."""

from __future__ import annotations

import json

from homewatch import probes, til, timeline
from homewatch.models import Probe, Release
from homewatch.sources.base import upsert_release


def _seed(db):
    upsert_release(db, Release(product="homepod_software", version="18.4", source="x",
                              channel="stable", released_at="2026-04-15T17:02:00Z"))
    upsert_release(db, Release(product="home_assistant_core", version="2026.4.3",
                              source="x", channel="stable",
                              released_at="2026-04-16T09:00:00Z"))
    upsert_release(db, Release(product="home_assistant_core", version="2026.5.0b1",
                              source="x", channel="beta",
                              released_at="2026-04-30T12:00:00Z"))
    til.record(db, kind="down", target="homepod-kitchen", text="siri dead",
               at="2026-04-15T19:42:00Z")
    til.record(db, kind="up", target="homepod-kitchen", at="2026-04-15T20:11:00Z")
    probes.insert_probe(db, Probe(target_kind="home_assistant", target_id="ha",
                                  version="2026.4.3", probed_at="2026-04-16T09:05:00Z"))


def test_timeline_is_time_ordered(db):
    _seed(db)
    items = timeline.build(db)
    ts = [it["t"] for it in items]
    assert ts == sorted(ts)


def test_timeline_interleaves_releases_and_til(db):
    _seed(db)
    kinds = [it["kind"] for it in timeline.build(db)]
    assert "release" in kinds and "til" in kinds and "probe" in kinds


def test_betas_excluded_by_default(db):
    _seed(db)
    versions = {it.get("version") for it in timeline.build(db) if it["kind"] == "release"}
    assert "2026.5.0b1" not in versions
    versions_with = {
        it.get("version")
        for it in timeline.build(db, include_betas=True)
        if it["kind"] == "release"
    }
    assert "2026.5.0b1" in versions_with


def test_product_filter(db):
    _seed(db)
    rels = [
        it for it in timeline.build(db, products=["homepod_software"])
        if it["kind"] == "release"
    ]
    assert {it["product"] for it in rels} == {"homepod_software"}


def test_since_until_window(db):
    _seed(db)
    items = timeline.build(db, since="2026-04-16T00:00:00Z")
    assert all(it["t"] >= "2026-04-16T00:00:00Z" for it in items)


def test_json_and_md_and_html_render(db):
    _seed(db)
    items = timeline.build(db)
    parsed = json.loads(timeline.to_json(items))
    assert "items" in parsed and len(parsed["items"]) == len(items)
    assert timeline.render_md(items).startswith("# homewatch timeline")
    html = timeline.render_html(items)
    assert "<title>homewatch timeline</title>" in html
    assert "homepod_software 18.4" in html
