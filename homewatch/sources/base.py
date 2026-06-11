"""Source protocol + shared persistence/parse helpers. See spec §3.

A Source pulls release metadata from one upstream (a feed or a scraped page)
and yields :class:`Release` objects. The orchestrator (:func:`refresh`) handles
dedupe-on-insert, source_state bookkeeping, and error capture so individual
sources stay small.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import httpx
from selectolax.parser import HTMLParser

from ..db import utcnow
from ..models import Release, SourceState

# Stored notes are a short summary (spec §12.1); full bodies are fetched on
# demand by notes.fetch_full_notes.
SUMMARY_LIMIT = 280


@runtime_checkable
class Source(Protocol):
    name: str  # stable identifier, stored in releases.source
    products: list[str]  # which `product` values it can emit

    async def fetch(
        self, client: httpx.AsyncClient, state: SourceState
    ) -> list[Release]:
        """Pull and parse upstream into Release rows.

        May consult ``state.etag`` / ``state.last_modified`` to skip work and
        return ``[]``. A shared client (carrying the configured User-Agent) is
        passed in so every source is polite by default.
        """
        ...


# --- version / channel parsing -------------------------------------------------

# No leading \b: HA tags like "2026.5.0rc1" glue the digit to "rc". Guard
# against matching mid-word ("Barcelona1") with a non-letter lookbehind.
_RC_RE = re.compile(r"(?<![a-z])rc\.?\s*\d+", re.IGNORECASE)
_BETA_RE = re.compile(r"(\bbeta\b|\bb\d+\b|\d+b\d+\b)", re.IGNORECASE)


def clean_version(text: str) -> str:
    """Strip a leading ``v`` and surrounding whitespace from a tag/title."""
    return re.sub(r"^v(?=\d)", "", text.strip())


def detect_channel(text: str) -> str:
    """Classify a version/title into 'stable' | 'beta' | 'rc'.

    Defaults to 'stable' rather than NULL: a NULL channel would defeat the
    ``UNIQUE(product, version, channel)`` dedupe (SQLite treats NULLs as
    distinct), so every release carries an explicit channel.
    """
    if _RC_RE.search(text):
        return "rc"
    if _BETA_RE.search(text):
        return "beta"
    return "stable"


def feed_published_iso(entry) -> str | None:
    """ISO-8601 UTC from a feedparser entry's published/updated struct_time."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return (
        datetime(*parsed[:6], tzinfo=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


_DATE_FORMATS = (
    "%B %d, %Y",   # January 22, 2026
    "%b %d, %Y",   # Jan 22, 2026
    "%d %B %Y",    # 22 January 2026
    "%d %b %Y",    # 22 Jan 2026
    "%Y-%m-%d",    # 2026-01-22
)


def parse_human_date(text: str | None) -> str | None:
    """Best-effort parse of an Apple-style date string to an ``YYYY-MM-DD`` ISO date."""
    if not text:
        return None
    s = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def summarize(text: str | None, limit: int = SUMMARY_LIMIT) -> str | None:
    """Collapse whitespace and trim to a word-boundary excerpt (spec §12.1)."""
    if not text:
        return None
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0] + "…"


def feed_content_text(entry) -> str | None:
    """Plain text from a feedparser entry's content/summary (HTML stripped)."""
    raw = ""
    if entry.get("content"):
        raw = entry["content"][0].get("value", "")
    if not raw:
        raw = entry.get("summary", "")
    if not raw:
        return None
    if "<" in raw:
        raw = HTMLParser(raw).text(separator=" ", strip=True)
    return raw or None


def sha1(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


# --- source_state persistence --------------------------------------------------


def load_state(conn: sqlite3.Connection, name: str) -> SourceState:
    row = conn.execute(
        "SELECT * FROM source_state WHERE source=?", (name,)
    ).fetchone()
    if row is None:
        return SourceState(source=name)
    return SourceState(
        source=row["source"],
        last_fetched_at=row["last_fetched_at"],
        last_status=row["last_status"],
        etag=row["etag"],
        last_modified=row["last_modified"],
    )


def save_state(conn: sqlite3.Connection, state: SourceState) -> None:
    conn.execute(
        "INSERT INTO source_state"
        " (source, last_fetched_at, last_status, etag, last_modified)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(source) DO UPDATE SET"
        "   last_fetched_at=excluded.last_fetched_at,"
        "   last_status=excluded.last_status,"
        "   etag=excluded.etag,"
        "   last_modified=excluded.last_modified",
        (
            state.source,
            state.last_fetched_at,
            state.last_status,
            state.etag,
            state.last_modified,
        ),
    )


def conditional_headers(state: SourceState) -> dict[str, str]:
    """Build If-None-Match / If-Modified-Since headers from prior state."""
    headers: dict[str, str] = {}
    if state.etag:
        headers["If-None-Match"] = state.etag
    if state.last_modified:
        headers["If-Modified-Since"] = state.last_modified
    return headers


# --- release persistence -------------------------------------------------------


def upsert_release(conn: sqlite3.Connection, r: Release) -> bool:
    """Insert a release; if it already exists, gap-fill its NULL columns.

    Returns True only when a *new* row is inserted (so refresh counts stay
    honest and idempotent). The gap-fill merges complementary sources without
    clobbering existing data: e.g. apple_security supplies a HomePod release
    date while homepod_notes supplies the notes — COALESCE keeps both, in
    whichever order the two refreshes run (spec §3.4/§3.6).
    """
    channel = r.channel or "stable"
    existing = conn.execute(
        "SELECT id FROM releases WHERE product=? AND version=? AND channel=?",
        (r.product, r.version, channel),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE releases SET"
            "  released_at = COALESCE(released_at, ?),"
            "  title       = COALESCE(title, ?),"
            "  url         = COALESCE(url, ?),"
            "  notes       = COALESCE(notes, ?)"
            " WHERE id = ?",
            (r.released_at, r.title, r.url, r.notes, existing["id"]),
        )
        return False
    conn.execute(
        "INSERT INTO releases"
        " (product, version, channel, released_at, title, url, source, raw_id,"
        "  notes, discovered_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            r.product,
            r.version,
            channel,
            r.released_at,
            r.title,
            r.url,
            r.source,
            r.raw_id,
            r.notes,
            utcnow(),
        ),
    )
    return True


def list_releases(
    conn: sqlite3.Connection,
    *,
    product: str | None = None,
    since: str | None = None,
    until: str | None = None,
    channel: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """Filtered release list, newest first. All filters optional (spec §5.1)."""
    where: list[str] = []
    params: list[object] = []
    if product:
        where.append("product = ?")
        params.append(product)
    if since:
        where.append("released_at >= ?")
        params.append(since)
    if until:
        where.append("released_at <= ?")
        params.append(until)
    if channel:
        where.append("channel = ?")
        params.append(channel)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    return conn.execute(
        "SELECT * FROM releases" + clause
        + " ORDER BY released_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()


def latest_release(
    conn: sqlite3.Connection, product: str, channel: str = "stable"
) -> sqlite3.Row | None:
    """Single most-recent release for a product on a channel (spec §5.1)."""
    return conn.execute(
        "SELECT * FROM releases WHERE product=? AND channel=?"
        " ORDER BY released_at DESC, id DESC LIMIT 1",
        (product, channel),
    ).fetchone()
