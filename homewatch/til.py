"""TIL / event log: parsing + persistence for human-entered observations.

The three input modes (web form, URL drop-in, CLI) all funnel into
:func:`record` writing to the ``til_events`` table. See spec §5.3.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .db import utcnow
from .models import TIL_KINDS, TilEvent


def parse_tags(raw: str | list[str] | None) -> list[str]:
    """Accept a comma-separated string (``upgrade,maybe-fixed``) or a list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = raw.split(",")
    return [t.strip() for t in items if t and t.strip()]


def normalize_timestamp(raw: str | None) -> str:
    """Normalize a user-supplied ``at=`` to ISO-8601 UTC; default to now.

    Lenient: accepts ``2026-04-15T19:42:00Z``, ``2026-04-15 19:42``, or a bare
    date. Naive timestamps are assumed UTC. Unparseable input falls back to now.
    """
    if not raw:
        return utcnow()
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T"))
        except ValueError:
            return utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k not in TIL_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {TIL_KINDS}")
    return k


def record(
    conn: sqlite3.Connection,
    *,
    kind: str,
    target: str | None = None,
    text: str = "",
    tags: str | list[str] | None = None,
    at: str | None = None,
    source: str | None = None,
) -> int:
    """Validate, normalize, and insert one event. Returns the new row id."""
    ev = TilEvent(
        kind=normalize_kind(kind),
        target=target or None,
        text=text or kind,  # `text` defaults to the kind word if absent (spec §5.3)
        tags=parse_tags(tags),
        occurred_at=normalize_timestamp(at),
        recorded_at=utcnow(),
        source=source,
    )
    cur = conn.execute(
        "INSERT INTO til_events"
        " (occurred_at, recorded_at, kind, target, text, tags, source)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            ev.occurred_at,
            ev.recorded_at,
            ev.kind,
            ev.target,
            ev.text,
            json.dumps(ev.tags),
            ev.source,
        ),
    )
    return int(cur.lastrowid)


def _row_to_event(row: sqlite3.Row) -> TilEvent:
    return TilEvent(
        id=row["id"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        kind=row["kind"],
        target=row["target"],
        text=row["text"],
        tags=json.loads(row["tags"]) if row["tags"] else [],
        source=row["source"],
    )


def query(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    kind: str | None = None,
    target: str | None = None,
    limit: int = 200,
    include_deleted: bool = False,
) -> list[TilEvent]:
    """Reverse-chronological event list with optional filters."""
    where = []
    params: list[object] = []
    if since:
        where.append("occurred_at >= ?")
        params.append(since)
    if until:
        where.append("occurred_at <= ?")
        params.append(until)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if target:
        where.append("target = ?")
        params.append(target)
    if not include_deleted:
        where.append("kind != 'deleted'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT * FROM til_events" + clause + " ORDER BY occurred_at DESC LIMIT ?"
    )
    params.append(limit)
    return [_row_to_event(r) for r in conn.execute(sql, params).fetchall()]


def soft_delete(conn: sqlite3.Connection, event_id: int) -> bool:
    """Soft-delete (kind='deleted') — append-mostly, keep the row. See spec §5.3."""
    cur = conn.execute(
        "UPDATE til_events SET kind='deleted' WHERE id=? AND kind!='deleted'",
        (event_id,),
    )
    return cur.rowcount > 0


def render_tsv(events: list[TilEvent]) -> str:
    """Tab-separated rows for grep/awk. Columns: id occurred kind target tags text."""
    lines = ["id\toccurred_at\tkind\ttarget\ttags\ttext"]
    for e in events:
        lines.append(
            "\t".join(
                [
                    str(e.id or ""),
                    e.occurred_at or "",
                    e.kind,
                    e.target or "",
                    ",".join(e.tags),
                    (e.text or "").replace("\t", " ").replace("\n", " "),
                ]
            )
        )
    return "\n".join(lines) + "\n"
