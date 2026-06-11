"""Configuration via pydantic-settings (.env / HOMEWATCH_* env vars). See spec §6."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEWATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db: Path = Path("data/homewatch.sqlite")
    # Optional bearer; if set, all routes except GET /til/drop/... require it.
    token: str | None = None
    # If set, the CLI drives this remote daemon instead of the local DB (§11.1).
    url: str | None = None

    ha_url: str = "http://hass.local:8123"
    ha_token: str | None = None

    homepod_discovery: Literal["pyatv", "zeroconf", "disabled"] = "disabled"

    bind: str = "127.0.0.1:8765"
    user_agent: str = "homewatch/0.1 (+https://github.com/mcint/homewatch)"

    @property
    def bind_host(self) -> str:
        return self.bind.rsplit(":", 1)[0]

    @property
    def bind_port(self) -> int:
        return int(self.bind.rsplit(":", 1)[1])


@lru_cache
def get_settings() -> Settings:
    return Settings()
