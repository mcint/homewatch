"""Release-source registry + the refresh orchestrator. See spec §3, §5.1."""

from __future__ import annotations

import sqlite3

import httpx

from ..db import utcnow
from .apple_developer import AppleDeveloperSource
from .apple_security import AppleSecuritySource
from .base import Source, load_state, save_state, upsert_release
from .ha_blog import HABlogSource
from .ha_os import HAOSSource
from .ha_release import HAReleaseSource
from .homepod_notes import HomePodNotesSource

# Order is cosmetic; refresh runs them sequentially to stay polite.
ALL_SOURCES: list[Source] = [
    HAReleaseSource(),
    HABlogSource(),
    HAOSSource(),
    AppleSecuritySource(),
    AppleDeveloperSource(),
    HomePodNotesSource(),
]

SOURCES_BY_NAME: dict[str, Source] = {s.name: s for s in ALL_SOURCES}


def select_sources(name: str | None) -> list[Source]:
    """Resolve ``?source=`` to a source list. ``None``/``*`` means all."""
    if name in (None, "", "*", "all"):
        return list(ALL_SOURCES)
    if name not in SOURCES_BY_NAME:
        raise KeyError(name)
    return [SOURCES_BY_NAME[name]]


async def refresh_source(
    conn: sqlite3.Connection, client: httpx.AsyncClient, source: Source
) -> dict:
    """Fetch one source, upsert its releases, update source_state.

    Returns ``{"new": int, "seen": int, "errors": [str, …]}``.
    """
    state = load_state(conn, source.name)
    result: dict = {"new": 0, "seen": 0, "errors": []}
    try:
        releases = await source.fetch(client, state)
        for rel in releases:
            if upsert_release(conn, rel):
                result["new"] += 1
            else:
                result["seen"] += 1
        # A source (e.g. homepod_notes / apple_developer) may set a warning
        # status during a successful fetch; preserve it, else mark ok.
        if not (state.last_status or "").startswith(("error", "warning")):
            state.last_status = "ok"
    except Exception as exc:  # noqa: BLE001 — capture per-source, never abort refresh
        msg = f"{type(exc).__name__}: {exc}"
        result["errors"].append(msg)
        state.last_status = f"error: {msg}"
    finally:
        state.last_fetched_at = utcnow()
        save_state(conn, state)
    return result


async def refresh(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    source: str | None = None,
) -> dict[str, dict]:
    """Refresh one or all sources. Returns counts keyed by source name."""
    out: dict[str, dict] = {}
    for src in select_sources(source):
        out[src.name] = await refresh_source(conn, client, src)
    return out
