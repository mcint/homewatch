"""LAN probes: HA REST probe + persistence/history."""

from __future__ import annotations

import json

import httpx
import pytest

from homewatch import probes
from homewatch.probes.ha import probe_ha
from homewatch.probes.homepod import probe_homepods


@pytest.mark.asyncio
async def test_probe_ha_success(httpx_mock):
    httpx_mock.add_response(
        url="http://hass.local:8123/api/config",
        json={"version": "2026.4.3", "installation_type": "Home Assistant OS"},
    )
    p = await probe_ha("http://hass.local:8123", "tok")
    assert p.version == "2026.4.3"
    assert p.error is None
    assert p.extra["installation_type"] == "Home Assistant OS"


@pytest.mark.asyncio
async def test_probe_ha_401_records_error(httpx_mock):
    httpx_mock.add_response(url="http://hass.local:8123/api/config", status_code=401)
    p = await probe_ha("http://hass.local:8123", "bad")
    assert p.version is None
    assert "401" in p.error


@pytest.mark.asyncio
async def test_probe_ha_connection_refused_is_signal(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    p = await probe_ha("http://hass.local:8123", "tok")
    assert p.version is None
    assert p.error  # HA being down is itself recorded


@pytest.mark.asyncio
async def test_probe_homepods_disabled_returns_empty():
    assert await probe_homepods("disabled") == []


@pytest.mark.asyncio
async def test_probe_homepods_unknown_backend():
    with pytest.raises(ValueError):
        await probe_homepods("carrier-pigeon")


def test_insert_and_history(db):
    from homewatch.models import Probe

    probes.insert_probe(db, Probe(target_kind="home_assistant", target_id="ha",
                                  version="2026.4.3", extra={"a": 1}))
    probes.insert_probe(db, Probe(target_kind="homepod", target_id="hp-1",
                                  version="18.4"))
    rows = probes.history(db)
    assert len(rows) == 2
    ha = probes.history(db, target_kind="home_assistant")
    assert len(ha) == 1
    assert json.loads(ha[0]["extra_json"]) == {"a": 1}
