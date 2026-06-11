"""Configuration via pydantic-settings (.env / HOMEWATCH_* env vars). See spec §6.

Data lives in a stable per-user location by default, so the CLI works the same
no matter which directory you run it from:

- ``HOMEWATCH_HOME`` set → that directory is the data-project root: DB at
  ``$HOMEWATCH_HOME/homewatch.sqlite`` and config at ``$HOMEWATCH_HOME/.env``.
- otherwise → XDG: DB under ``${XDG_DATA_HOME:-~/.local/share}/homewatch`` and
  ``.env`` read from the cwd and ``${XDG_CONFIG_HOME:-~/.config}/homewatch``.

``HOMEWATCH_DB`` always overrides the DB path explicitly.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _xdg(var: str, default_sub: str) -> Path:
    return Path(os.environ.get(var) or (Path.home() / default_sub))


def data_root() -> Path:
    """Directory holding the SQLite DB."""
    home = os.environ.get("HOMEWATCH_HOME")
    if home:
        return Path(home).expanduser()
    return _xdg("XDG_DATA_HOME", ".local/share") / "homewatch"


def config_root() -> Path:
    """Directory holding the .env (XDG config home / homewatch)."""
    home = os.environ.get("HOMEWATCH_HOME")
    if home:
        return Path(home).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "homewatch"


def default_db() -> Path:
    return data_root() / "homewatch.sqlite"


def env_files() -> tuple[str, ...]:
    """.env locations, lowest-precedence first (later ones win in pydantic)."""
    home = os.environ.get("HOMEWATCH_HOME")
    if home:
        return (str(Path(home).expanduser() / ".env"),)
    # cwd .env (dev convenience) then the per-user config .env.
    return (str(config_root() / ".env"), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEWATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Data-project root; when set, DB + .env live under it (see module docstring).
    home: Path | None = None
    db: Path = Field(default_factory=default_db)
    # Optional bearer; if set, all routes except GET /til/drop/... require it.
    token: str | None = None
    # If set, the CLI drives this remote daemon instead of the local DB (§11.1).
    url: str | None = None

    ha_url: str = "http://hass.local:8123"
    ha_token: str | None = None

    homepod_discovery: Literal["pyatv", "zeroconf", "disabled"] = "disabled"

    bind: str = "127.0.0.1:8765"
    user_agent: str = "homewatch/0.3 (+https://github.com/mcint/homewatch)"

    @property
    def bind_host(self) -> str:
        return self.bind.rsplit(":", 1)[0]

    @property
    def bind_port(self) -> int:
        return int(self.bind.rsplit(":", 1)[1])


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_files())
