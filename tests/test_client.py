"""Backend abstraction: LocalBackend (direct) and RemoteBackend (HTTP)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homewatch.client import LocalBackend, RemoteBackend, get_backend
from homewatch.config import Settings
from homewatch.sources.ha_release import URL as HA_CORE_URL

FIX = Path(__file__).parent / "fixtures"


def _settings(tmp_path, **kw) -> Settings:
    base = dict(db=tmp_path / "c.sqlite", homepod_discovery="disabled",
                url=None, token=None, ha_url="http://hass.local:8123",
                user_agent="homewatch-test")
    base.update(kw)
    return Settings(**base)


# --- LocalBackend --------------------------------------------------------------


async def test_local_refresh_til_timeline(tmp_path, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    async with LocalBackend(_settings(tmp_path)) as b:
        counts = await b.refresh("ha_core_atom")
        assert counts["ha_core_atom"]["new"] == 2

        rid = await b.til(kind="down", target="homepod-kitchen", text="siri",
                          tags="x", at=None, probe=False)
        assert rid > 0

        tl = json.loads(await b.timeline(since=None, until=None, products=None,
                                         include_betas=False, fmt="json"))
        kinds = {it["kind"] for it in tl["items"]}
        assert "release" in kinds and "til" in kinds


async def test_local_latest_and_sources(tmp_path, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    async with LocalBackend(_settings(tmp_path)) as b:
        await b.refresh("ha_core_atom")
        latest = await b.latest("home_assistant_core", "stable")
        assert latest["version"] == "2026.4.3"

        srcs = await b.sources()
        assert len(srcs) == 7
        core = next(s for s in srcs if s["name"] == "ha_core_atom")
        assert core["url"] == HA_CORE_URL
        assert core["last_status"] == "ok"


async def test_local_probe_homepods_disabled(tmp_path):
    async with LocalBackend(_settings(tmp_path)) as b:
        assert await b.probe_homepods() == []


async def test_local_show_fetches_full_notes(tmp_path, httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    httpx_mock.add_response(
        url="https://github.com/home-assistant/core/releases/tag/2026.4.3",
        html='<html><body><div class="markdown-body">Fixed the Matter bug.</div></body></html>',
    )
    async with LocalBackend(_settings(tmp_path)) as b:
        await b.refresh("ha_core_atom")
        shown = await b.show("home_assistant_core", "2026.4.3", "stable")
        assert "Matter" in shown["notes_full"]


async def test_local_show_missing_returns_none(tmp_path):
    async with LocalBackend(_settings(tmp_path)) as b:
        assert await b.show("home_assistant_core", "9.9.9", "stable") is None


# --- RemoteBackend -------------------------------------------------------------


async def test_remote_til_parses_ok_id(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="http://daemon.test/til/drop/down/ha?text=x&tags=&probe=false",
        text="OK 42\n",
    )
    async with RemoteBackend(_settings(tmp_path), "http://daemon.test") as b:
        rid = await b.til(kind="down", target="ha", text="x", tags=None,
                          at=None, probe=False)
        assert rid == 42


async def test_remote_refresh(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="http://daemon.test/releases/refresh?source=ha_core_atom",
        method="POST",
        json={"ha_core_atom": {"new": 3, "seen": 0, "errors": []}},
    )
    async with RemoteBackend(_settings(tmp_path), "http://daemon.test") as b:
        assert (await b.refresh("ha_core_atom"))["ha_core_atom"]["new"] == 3


async def test_remote_sends_bearer(httpx_mock, tmp_path):
    httpx_mock.add_response(url="http://daemon.test/releases/sources",
                            json={"sources": []})
    async with RemoteBackend(_settings(tmp_path, token="sek"), "http://daemon.test") as b:
        await b.sources()
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer sek"


# --- selection -----------------------------------------------------------------


def test_get_backend_selection(tmp_path):
    local = _settings(tmp_path)
    assert isinstance(get_backend(local), LocalBackend)
    assert isinstance(get_backend(local, remote="http://x"), RemoteBackend)
    remote_cfg = _settings(tmp_path, url="http://env")
    assert isinstance(get_backend(remote_cfg), RemoteBackend)
