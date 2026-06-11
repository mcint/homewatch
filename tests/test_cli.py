"""CLI: the thin HTTP client (outbound calls mocked)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from homewatch.cli import app
from homewatch.config import get_settings

runner = CliRunner()
BASE = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.delenv("HOMEWATCH_URL", raising=False)
    monkeypatch.delenv("HOMEWATCH_TOKEN", raising=False)
    monkeypatch.setenv("HOMEWATCH_BIND", "127.0.0.1:8765")
    get_settings.cache_clear()


def test_til_down_hits_drop_endpoint(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/til/drop/down/homepod-kitchen?text=siri+dead",
        text="OK 7\n",
    )
    result = runner.invoke(app, ["til", "down", "homepod-kitchen", "siri dead"])
    assert result.exit_code == 0
    assert "OK 7" in result.stdout


def test_til_note_with_tags(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/til/drop/note/ha?text=rebooted&tags=upgrade,maybe-fixed",
        text="OK 8\n",
    )
    result = runner.invoke(
        app, ["til", "note", "ha", "rebooted", "-t", "upgrade", "-t", "maybe-fixed"]
    )
    assert result.exit_code == 0
    assert "OK 8" in result.stdout


def test_refresh_prints_counts(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/releases/refresh?source=ha_core_atom",
        method="POST",
        json={"ha_core_atom": {"new": 2, "seen": 0, "errors": []}},
    )
    result = runner.invoke(app, ["refresh", "--source", "ha_core_atom"])
    assert result.exit_code == 0
    assert "ha_core_atom: +2 new" in result.stdout


def test_uses_homewatch_url_env(httpx_mock, monkeypatch):
    monkeypatch.setenv("HOMEWATCH_URL", "https://vps.example:9000")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://vps.example:9000/til/drop/up/ha", text="OK 1\n"
    )
    result = runner.invoke(app, ["til", "up", "ha"])
    assert result.exit_code == 0


def test_error_exit_code(httpx_mock):
    httpx_mock.add_response(
        url=f"{BASE}/til/drop/down/ha", status_code=500, text="boom"
    )
    result = runner.invoke(app, ["til", "down", "ha"])
    assert result.exit_code == 1
