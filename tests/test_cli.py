"""CLI: local-first by default, remote when --remote/HOMEWATCH_URL is set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from homewatch.cli import app, parse_duration
from homewatch.config import get_settings
from homewatch.sources.ha_release import URL as HA_CORE_URL

runner = CliRunner()
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _local_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEWATCH_DB", str(tmp_path / "cli.sqlite"))
    monkeypatch.setenv("HOMEWATCH_HOMEPOD_DISCOVERY", "disabled")
    monkeypatch.delenv("HOMEWATCH_URL", raising=False)
    monkeypatch.delenv("HOMEWATCH_TOKEN", raising=False)
    get_settings.cache_clear()


def test_til_writes_directly_then_timeline_shows_it():
    r = runner.invoke(app, ["til", "down", "homepod-kitchen", "siri dead"])
    assert r.exit_code == 0, r.output
    assert "OK 1" in r.output
    # Same local DB, no server: timeline reflects it.
    tl = runner.invoke(app, ["timeline", "--format", "json"])
    items = json.loads(tl.output)["items"]
    assert any(it["kind"] == "til" and it.get("text") == "siri dead" for it in items)


def test_refresh_then_latest(httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    r = runner.invoke(app, ["refresh", "--source", "ha_core_atom"])
    assert r.exit_code == 0, r.output
    assert "ha_core_atom: +2 new" in r.output
    out = runner.invoke(app, ["latest", "home_assistant_core"])
    assert "2026.4.3" in out.output


def test_show_fetches_notes(httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    httpx_mock.add_response(
        url="https://github.com/home-assistant/core/releases/tag/2026.4.3",
        html='<html><body><div class="markdown-body">Fixed Matter pairing.</div></body></html>',
    )
    runner.invoke(app, ["refresh", "--source", "ha_core_atom"])
    r = runner.invoke(app, ["show", "home_assistant_core", "2026.4.3"])
    assert r.exit_code == 0, r.output
    assert "Fixed Matter pairing." in r.output


def test_sources_lists_streams():
    r = runner.invoke(app, ["sources"])
    assert r.exit_code == 0
    assert "ha_core_atom" in r.output
    assert "github.com/home-assistant/core" in r.output


def test_probe_homepods_disabled_message():
    r = runner.invoke(app, ["probe", "homepods"])
    assert r.exit_code == 0
    assert "no homepods" in r.output


def test_remote_mode_uses_daemon(httpx_mock):
    httpx_mock.add_response(
        url="http://daemon.test/til/drop/up/homepod-kitchen?text=&tags=&probe=true",
        text="OK 5\n",
    )
    r = runner.invoke(app, ["--remote", "http://daemon.test", "til", "up",
                            "homepod-kitchen"])
    assert r.exit_code == 0, r.output
    assert "OK 5" in r.output


def test_latest_missing_exits_1():
    r = runner.invoke(app, ["latest", "home_assistant_core"])
    assert r.exit_code == 1


def test_unknown_product_rejected_with_hint():
    r = runner.invoke(app, ["latest", "bogusos"])
    assert r.exit_code != 0
    assert "unknown product" in r.output and "homepod_software" in r.output


def test_products_lists_vocabulary():
    r = runner.invoke(app, ["products"])
    assert r.exit_code == 0
    for pid in ("homepod_software", "tvos", "home_assistant_core"):
        assert pid in r.output
    assert "tracks tvOS" in r.output


def test_releases_default_window_is_recent_stable(httpx_mock):
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    runner.invoke(app, ["refresh", "--source", "ha_core_atom"])
    # ha_core.atom has a stable (2026.4.3) and a beta (2026.5.0b1); default
    # excludes the beta. Both dates are old, so use --months 0 to see all.
    out = runner.invoke(app, ["releases", "--months", "0"]).output
    assert "2026.4.3" in out and "2026.5.0b1" not in out
    # --all surfaces the beta too.
    all_out = runner.invoke(app, ["releases", "--all"]).output
    assert "2026.5.0b1" in all_out


def test_parse_duration():
    assert parse_duration("30s") == 30
    assert parse_duration("5m") == 300
    assert parse_duration("1h") == 3600
    assert parse_duration("7d") == 604800
    assert parse_duration("0") == 0
    assert parse_duration("45") == 45


def test_version_flag():
    from homewatch import __version__
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and __version__ in r.output
    assert runner.invoke(app, ["-V"]).exit_code == 0


def test_dash_h_works_top_and_sub():
    assert runner.invoke(app, ["-h"]).exit_code == 0
    sub = runner.invoke(app, ["til", "-h"])
    assert sub.exit_code == 0 and "down" in sub.output


def test_info_shows_db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEWATCH_DB", str(tmp_path / "info.sqlite"))
    get_settings.cache_clear()
    r = runner.invoke(app, ["info"])
    assert r.exit_code == 0
    assert "info.sqlite" in r.output


def test_watch_until_new_exits_when_release_lands(httpx_mock):
    # First (and only) cycle finds 2 new releases -> --until-new returns at once.
    httpx_mock.add_response(url=HA_CORE_URL, content=(FIX / "ha_core.atom").read_bytes())
    r = runner.invoke(app, ["watch", "--source", "ha_core_atom", "--until-new",
                            "--interval", "1h"])
    assert r.exit_code == 0, r.output
    assert "new release(s): 2" in r.output
