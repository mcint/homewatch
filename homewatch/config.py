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

from . import __version__


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
    """Config-file locations, lowest-precedence first (later ones win in pydantic).

    The per-user XDG config file is named ``env`` (no dot — it lives in a
    dedicated config dir, so it needn't be hidden); the cwd dev-convenience file
    stays ``.env``.
    """
    home = os.environ.get("HOMEWATCH_HOME")
    if home:
        return (str(Path(home).expanduser() / "env"),)
    return (str(config_root() / "env"), ".env")


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
    user_agent: str = f"homewatch/{__version__} (+https://github.com/mcint/homewatch)"

    @property
    def bind_host(self) -> str:
        return self.bind.rsplit(":", 1)[0]

    @property
    def bind_port(self) -> int:
        return int(self.bind.rsplit(":", 1)[1])


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=env_files())


# --- writable config file (persist settings once) -----------------------------

SECRET_KEYS = frozenset({"token", "ha_token"})


def setting_keys() -> tuple[str, ...]:
    """Settable HOMEWATCH_* keys (the Settings fields)."""
    return tuple(Settings.model_fields)


def config_file() -> Path:
    """The per-user config file we write to (HOMEWATCH_HOME/env or XDG env)."""
    home = os.environ.get("HOMEWATCH_HOME")
    base = Path(home).expanduser() if home else config_root()
    return base / "env"


def normalize_key(key: str) -> str:
    """Accept 'homepod_discovery' or 'HOMEWATCH_HOMEPOD_DISCOVERY' → field name."""
    k = key.strip().lower()
    if k.startswith("homewatch_"):
        k = k[len("homewatch_"):]
    if k not in Settings.model_fields:
        raise KeyError(key)
    return k


def write_config_value(key: str, value: str) -> Path:
    """Upsert ``HOMEWATCH_<KEY>=value`` in the per-user config file (dotenv).

    Preserves other lines/comments. Returns the file path. Raises KeyError for
    an unknown setting.
    """
    field = normalize_key(key)
    env_key = f"HOMEWATCH_{field.upper()}"
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for ln in lines:
        if ln.lstrip().startswith(f"{env_key}="):
            out.append(f"{env_key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{env_key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path
