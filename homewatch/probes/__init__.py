"""LAN probe persistence + history. See spec §4, §5.2."""

from __future__ import annotations

import json
import sqlite3

from ..db import utcnow
from ..models import Probe
from .ha import probe_ha
from .homepod import probe_homepods

__all__ = ["probe_ha", "probe_homepods", "insert_probe", "history"]


def insert_probe(conn: sqlite3.Connection, probe: Probe) -> int:
    """Persist one probe row (success or failure). Returns the new id."""
    probed_at = probe.probed_at or utcnow()
    extra_json = json.dumps(probe.extra) if probe.extra is not None else None
    cur = conn.execute(
        "INSERT INTO probes"
        " (probed_at, target_kind, target_id, version, extra_json, error)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            probed_at,
            probe.target_kind,
            probe.target_id,
            probe.version,
            extra_json,
            probe.error,
        ),
    )
    return int(cur.lastrowid)


def history(
    conn: sqlite3.Connection,
    *,
    target_kind: str | None = None,
    target_id: str | None = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Recent probe rows, newest first (spec §5.2)."""
    where: list[str] = []
    params: list[object] = []
    if target_kind:
        where.append("target_kind = ?")
        params.append(target_kind)
    if target_id:
        where.append("target_id = ?")
        params.append(target_id)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    return conn.execute(
        "SELECT * FROM probes" + clause + " ORDER BY probed_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
