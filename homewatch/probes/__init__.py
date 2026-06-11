"""LAN probe persistence + history. See spec §4, §5.2."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import TYPE_CHECKING

import httpx

from .. import devices
from ..db import utcnow
from ..models import Probe
from .ha import probe_ha
from .homepod import discover_raw, probe_homepods

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger("homewatch.probes")

__all__ = ["probe_ha", "probe_homepods", "discover_raw", "insert_probe",
           "history", "autoprobe", "observe"]


def _device_identity(probe: Probe) -> dict:
    """Derive (device_id, kind, product, name, identifiers, mac) from a probe."""
    extra = probe.extra or {}
    if probe.target_kind == "homepod":
        mac = probe.mac or extra.get("mac") or extra.get("deviceid") or probe.target_id
        device_id = probe.target_id or mac
        # A friendly name only — never the MAC/identifier (which scans sometimes
        # report as the name). None means "leave the stored name as-is".
        raw = extra.get("name")
        friendly = raw if raw and raw not in (mac, device_id) else None
        identifiers = {"mac": mac}
        if friendly:
            identifiers["mdns"] = friendly
        return {
            "device_id": device_id,
            "kind": "homepod",
            "product": "homepod_software",
            "name": friendly,
            "identifiers": identifiers,
            "mac": mac,
        }
    install = (extra.get("installation_type") or "").lower()
    return {
        "device_id": probe.target_id,
        "kind": "home_assistant",
        "product": "home_assistant_os" if "os" in install else "home_assistant_core",
        "name": probe.target_id,
        "identifiers": {"url": probe.target_id},
        "mac": None,
    }


def observe(
    conn: sqlite3.Connection, probe: Probe, *,
    ssid: str | None = None, subnet: str | None = None,
) -> int:
    """Persist a probe row AND auto-enroll/update its device (spec §13).

    A successful probe is a sighting: it upserts the device (last_seen, version,
    network context) and writes an `enrolled` event on first sight. Failures are
    still recorded as probe rows (HA down is signal) but don't enroll.

    ``ssid``/``subnet`` describe the *prober's* network; the device IP comes
    from the probe itself (the prober's own IP is not the device's).
    """
    ident = _device_identity(probe)
    logger.info("probe %s %s → %s", ident["kind"], ident["device_id"],
                probe.version or f"FAILED ({probe.error})")
    probe.device_id = ident["device_id"]
    probe.mac = probe.mac or ident["mac"]
    probe.ssid = probe.ssid or ssid
    probe.subnet = probe.subnet or subnet
    if probe.error is None:
        devices.record_sighting(
            conn, device_id=ident["device_id"], kind=ident["kind"],
            version=probe.version, product=ident["product"], name=ident["name"],
            identifiers=ident["identifiers"], ssid=probe.ssid, subnet=probe.subnet,
            ip=probe.ip,
        )
    return insert_probe(conn, probe)


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
            ids.append(observe(conn, probe))  # auto-enroll; netinfo skipped (fast path)
        if t.startswith("homepod") and settings.homepod_discovery != "disabled":
            for probe in await probe_homepods(settings.homepod_discovery):
                ids.append(observe(conn, probe))
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
