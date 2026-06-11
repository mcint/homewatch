"""Device registry: enroll / view / retire + auto-enroll-on-sighting. Spec §13.

A device is anything we track a running version / presence for (HomePod, Home
Assistant, ESPHome, a self-reporting app). Identity is a stable ``device_id``
with an ``identifiers`` JSON blob, so we're not locked to MAC.

Sightings (probes) auto-enroll unknown devices and write an 'enrolled' event to
the same event log the TIL entries use, so detections show up on the timeline.
"""

from __future__ import annotations

import json
import sqlite3

from .db import utcnow
from .models import Device


def _row_to_device(row: sqlite3.Row) -> Device:
    return Device(
        device_id=row["device_id"],
        kind=row["kind"],
        product=row["product"],
        name=row["name"],
        display_name=row["display_name"],
        identifiers=json.loads(row["identifiers"]) if row["identifiers"] else {},
        enrolled_at=row["enrolled_at"],
        last_seen_at=row["last_seen_at"],
        last_version=row["last_version"],
        status=row["status"],
        ssid=row["ssid"],
        subnet=row["subnet"],
        ip=row["ip"],
        notes=row["notes"],
    )


def get(conn: sqlite3.Connection, device_id: str) -> Device | None:
    row = conn.execute(
        "SELECT * FROM devices WHERE device_id=?", (device_id,)
    ).fetchone()
    return _row_to_device(row) if row else None


def list_devices(
    conn: sqlite3.Connection, *, kind: str | None = None,
    include_retired: bool = False,
) -> list[Device]:
    where, params = [], []
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if not include_retired:
        where.append("status != 'retired'")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        "SELECT * FROM devices" + clause + " ORDER BY last_seen_at DESC, name", params
    ).fetchall()
    return [_row_to_device(r) for r in rows]


def _log_event(conn: sqlite3.Connection, kind: str, target: str | None, text: str) -> None:
    """Append a device-lifecycle event to the shared event log (timeline-visible)."""
    now = utcnow()
    conn.execute(
        "INSERT INTO til_events"
        " (occurred_at, recorded_at, kind, target, text, tags, source)"
        " VALUES (?, ?, ?, ?, ?, ?, 'device')",
        (now, now, kind, target, text, "[]"),
    )


def enroll(
    conn: sqlite3.Connection,
    device_id: str,
    kind: str,
    *,
    product: str | None = None,
    name: str | None = None,
    identifiers: dict | None = None,
) -> tuple[Device, bool]:
    """Register a device (or update its descriptive fields). Returns (device, created)."""
    existing = get(conn, device_id)
    if existing is None:
        now = utcnow()
        conn.execute(
            "INSERT INTO devices"
            " (device_id, kind, product, name, identifiers, enrolled_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, 'active')",
            # name may be NULL (no friendly name yet) — list/display fall back to
            # device_id, so we don't echo the MAC in the name column.
            (device_id, kind, product, name, json.dumps(identifiers or {}), now),
        )
        _log_event(conn, "enrolled", name or device_id,
                   f"enrolled {kind} {name or device_id}")
        return get(conn, device_id), True

    # Update descriptive fields without clobbering with NULLs.
    merged = {**existing.identifiers, **(identifiers or {})}
    conn.execute(
        "UPDATE devices SET kind=?, product=COALESCE(?, product),"
        "  name=COALESCE(?, name), identifiers=?, status='active' WHERE device_id=?",
        (kind, product, name, json.dumps(merged), device_id),
    )
    return get(conn, device_id), False


def record_sighting(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    kind: str,
    version: str | None = None,
    product: str | None = None,
    name: str | None = None,
    identifiers: dict | None = None,
    ssid: str | None = None,
    subnet: str | None = None,
    ip: str | None = None,
) -> bool:
    """Upsert a device from a probe sighting (auto-enroll). Returns True if new.

    Updates last_seen / last_version / network context; the probe row itself is
    inserted separately by the probe path.
    """
    _, created = enroll(conn, device_id, kind, product=product, name=name,
                        identifiers=identifiers)
    conn.execute(
        "UPDATE devices SET last_seen_at=?, last_version=COALESCE(?, last_version),"
        "  ssid=COALESCE(?, ssid), subnet=COALESCE(?, subnet), ip=COALESCE(?, ip)"
        " WHERE device_id=?",
        (utcnow(), version, ssid, subnet, ip, device_id),
    )
    return created


def rename(conn: sqlite3.Connection, device_id: str, display_name: str | None) -> bool:
    """Set (or clear, with None/'') a device's display name. Returns True if found."""
    cur = conn.execute(
        "UPDATE devices SET display_name=? WHERE device_id=?",
        (display_name or None, device_id),
    )
    return cur.rowcount > 0


def display(dev: Device) -> str:
    """The name to show: user display_name > detected name > device_id."""
    return dev.display_name or dev.name or dev.device_id


def retire(conn: sqlite3.Connection, device_id: str) -> bool:
    """Mark a device retired (dead/gone) and log the event. Returns True if changed."""
    cur = conn.execute(
        "UPDATE devices SET status='retired' WHERE device_id=? AND status!='retired'",
        (device_id,),
    )
    if cur.rowcount:
        dev = get(conn, device_id)
        _log_event(conn, "retired", dev.name if dev else device_id,
                   f"retired {device_id}")
        return True
    return False
