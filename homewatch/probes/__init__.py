"""LAN probe persistence + history. See spec §4, §5.2."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import httpx

from ..db import utcnow
from ..models import Probe
from .ha import probe_ha
from .homepod import discover_raw, probe_homepods

if TYPE_CHECKING:
    from ..config import Settings

__all__ = ["probe_ha", "probe_homepods", "discover_raw", "insert_probe",
           "history", "autoprobe"]


def insert_probe(conn: sqlite3.Connection, probe: Probe) -> int:
    """Persist one probe row (success or failure). Returns the new id."""
    probed_at = probe.probed_at or utcnow()
    extra_json = json.dumps(probe.extra) if probe.extra is not None else None
    cur = conn.execute(
        "INSERT INTO probes"
        " (probed_at, target_kind, target_id, version, extra_json, error,"
        "  device_id, ssid, ip, subnet, mac)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            probed_at,
            probe.target_kind,
            probe.target_id,
            probe.version,
            extra_json,
            probe.error,
            probe.device_id,
            probe.ssid,
            probe.ip,
            probe.subnet,
            probe.mac,
        ),
    )
    return int(cur.lastrowid)


async def autoprobe(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    target: str | None,
    settings: "Settings",
) -> list[int]:
    """Best-effort probe of the named target, persisting any rows (spec §9.2).

    Fires when a TIL target names HA (``ha`` / ``ha+homepod``) or a HomePod
    (``homepod*``, only if discovery is enabled). Never raises — auto-probe is a
    bonus that must not break the write that triggered it. Returns inserted ids.
    """
    ids: list[int] = []
    if not target:
        return ids
    t = target.lower()
    try:
        if t == "ha" or "ha" in t.split("+"):
            probe = await probe_ha(
                settings.ha_url, settings.ha_token, client=client, timeout=5
            )
            ids.append(insert_probe(conn, probe))
        if t.startswith("homepod") and settings.homepod_discovery != "disabled":
            for probe in await probe_homepods(settings.homepod_discovery):
                ids.append(insert_probe(conn, probe))
    except Exception:  # noqa: BLE001 — bonus probe, must not break the caller
        pass
    return ids


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
