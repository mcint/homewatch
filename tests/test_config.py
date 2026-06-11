"""Config data-root resolution (XDG / HOMEWATCH_HOME / HOMEWATCH_DB)."""

from __future__ import annotations

from pathlib import Path

from homewatch import config


def test_xdg_default_db(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMEWATCH_HOME", raising=False)
    monkeypatch.delenv("HOMEWATCH_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert config.default_db() == tmp_path / "data" / "homewatch" / "homewatch.sqlite"


def test_homewatch_home_root(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMEWATCH_HOME", str(tmp_path / "proj"))
    assert config.default_db() == tmp_path / "proj" / "homewatch.sqlite"
    assert config.env_files() == (str(tmp_path / "proj" / ".env"),)


def test_home_is_cwd_independent(monkeypatch, tmp_path):
    # Default DB does not depend on the current working directory.
    monkeypatch.delenv("HOMEWATCH_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    db1 = config.default_db()
    monkeypatch.chdir(tmp_path)
    assert config.default_db() == db1
    assert db1.is_absolute()


def test_explicit_db_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMEWATCH_DB", str(tmp_path / "x.sqlite"))
    monkeypatch.setenv("HOMEWATCH_HOME", str(tmp_path / "proj"))
    config.get_settings.cache_clear()
    assert config.get_settings().db == tmp_path / "x.sqlite"
    config.get_settings.cache_clear()
