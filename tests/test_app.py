"""HTTP surface: routes, content negotiation, auth, auto-probe."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from homewatch import app as app_module
from homewatch.config import get_settings
from homewatch.sources.ha_release import URL as HA_CORE_URL

FIX = Path(__file__).parent / "fixtures"


def make_client(tmp_path, monkeypatch, token: str | None = None) -> TestClient:
    monkeypatch.setenv("HOMEWATCH_DB", str(tmp_path / "app.sqlite"))
    monkeypatch.setenv("HOMEWATCH_HA_URL", "http://hass.local:8123")
    monkeypatch.setenv("HOMEWATCH_HOMEPOD_DISCOVERY", "disabled")
    if token:
        monkeypatch.setenv("HOMEWATCH_TOKEN", token)
    else:
        monkeypatch.delenv("HOMEWATCH_TOKEN", raising=False)
    get_settings.cache_clear()
    return TestClient(app_module.app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_til_drop_plain_returns_ok_id(client):
    r = client.get("/til/drop/down/homepod-kitchen", params={"text": "siri dead"})
    assert r.status_code == 200
    assert r.text.startswith("OK ")
    # The row is queryable.
    events = client.get("/til", params={"format": "json"}).json()["events"]
    assert events[0]["kind"] == "down"
    assert events[0]["target"] == "homepod-kitchen"
    assert events[0]["text"] == "siri dead"


def test_til_drop_browser_redirects(client):
    r = client.get(
        "/til/drop/up/homepod-kitchen",
        headers={"accept": "text/html"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/til"


def test_til_drop_bad_kind_400(client):
    assert client.get("/til/drop/exploded/ha").status_code == 400


def test_til_formats(client):
    client.get("/til/drop/note/ha", params={"text": "hi", "tags": "a,b", "probe": "false"})
    assert "OK" not in client.get("/til").text  # html form page
    assert "<form" in client.get("/til").text
    tsv = client.get("/til", params={"format": "tsv"}).text
    assert tsv.startswith("id\toccurred_at")


def test_til_post_form_and_delete(client):
    client.post("/til", data={"kind": "note", "target": "ha", "text": "typo"})
    eid = client.get("/til", params={"format": "json"}).json()["events"][0]["id"]
    assert client.delete(f"/til/{eid}").json() == {"deleted": eid}
    assert client.get("/til", params={"format": "json"}).json()["events"] == []
    assert client.delete(f"/til/{eid}").status_code == 404


def test_refresh_and_list_releases(client, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    counts = client.post("/releases/refresh", params={"source": "ha_core_atom"}).json()
    assert counts["ha_core_atom"]["new"] == 2

    releases = client.get("/releases", params={"product": "home_assistant_core"}).json()
    assert len(releases["releases"]) == 2

    latest = client.get(
        "/releases/latest",
        params={"product": "home_assistant_core", "channel": "stable"},
    ).json()
    assert latest["version"] == "2026.4.3"


def test_refresh_unknown_source_404(client):
    assert client.post("/releases/refresh", params={"source": "nope"}).status_code == 404


def test_releases_sources_status(client):
    out = client.get("/releases/sources").json()["sources"]
    names = {s["name"] for s in out}
    assert "ha_core_atom" in names and "endoflife" in names and len(out) == 7


def test_probe_ha_route(client, httpx_mock):
    httpx_mock.add_response(
        url="http://hass.local:8123/api/config",
        json={"version": "2026.4.3", "installation_type": "Home Assistant OS"},
    )
    r = client.post("/probe/ha").json()
    assert r["version"] == "2026.4.3" and r["ok"] is True


def test_probe_homepods_disabled(client):
    r = client.post("/probe/homepods").json()
    assert r["discovery"] == "disabled" and r["homepods"] == []


def test_probe_ingest_and_history(client):
    client.post("/probe/ingest", json={
        "target_kind": "homepod", "target_id": "hp-1", "version": "18.4",
    })
    hist = client.get("/probe/history", params={"target_kind": "homepod"}).json()
    assert hist["probes"][0]["version"] == "18.4"


def test_timeline_json_and_html(client, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    client.post("/releases/refresh", params={"source": "ha_core_atom"})
    client.get("/til/drop/down/ha", params={"text": "broke", "probe": "false"})

    items = client.get("/timeline").json()["items"]
    kinds = {it["kind"] for it in items}
    assert "release" in kinds and "til" in kinds

    html = client.get("/timeline", params={"format": "html"}).text
    assert "homewatch timeline" in html


def test_auth_gates_routes_but_not_drop(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch, token="s3cret") as client:
        # No token → 401 on a gated route.
        assert client.get("/releases").status_code == 401
        # Correct token → allowed.
        ok = client.get("/releases", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        # Drop-in stays open even with auth configured.
        drop = client.get("/til/drop/note/ha", params={"text": "x", "probe": "false"})
        assert drop.status_code == 200
