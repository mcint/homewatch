"""Device registry: enroll, auto-enroll-on-sighting, retire, events."""

from __future__ import annotations

from homewatch import devices, probes, til
from homewatch.models import Probe


def test_migration_added_devices_and_probe_columns(db):
    tables = {r["name"] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "devices" in tables
    cols = {r["name"] for r in db.execute("PRAGMA table_info(probes)")}
    assert {"device_id", "ssid", "ip", "subnet", "mac"} <= cols


def test_enroll_creates_then_updates(db):
    dev, created = devices.enroll(db, "AA:BB", "homepod", name="homepod-kitchen",
                                  product="homepod_software", identifiers={"mac": "AA:BB"})
    assert created and dev.name == "homepod-kitchen" and dev.status == "active"
    dev2, created2 = devices.enroll(db, "AA:BB", "homepod", identifiers={"mdns": "x"})
    assert created2 is False
    assert dev2.identifiers == {"mac": "AA:BB", "mdns": "x"}  # merged


def test_enroll_writes_event(db):
    devices.enroll(db, "AA:BB", "homepod", name="hk")
    events = til.query(db)
    assert any(e.kind == "enrolled" and e.target == "hk" and e.source == "device"
               for e in events)


def test_record_sighting_auto_enrolls_and_updates(db):
    new = devices.record_sighting(db, device_id="AA:BB", kind="homepod",
                                  version="18.4", product="homepod_software",
                                  name="hk", ssid="home", ip="10.0.0.5",
                                  subnet="10.0.0.0/24")
    assert new is True
    dev = devices.get(db, "AA:BB")
    assert dev.last_version == "18.4" and dev.ssid == "home" and dev.ip == "10.0.0.5"
    assert dev.last_seen_at is not None
    # Second sighting is not new.
    assert devices.record_sighting(db, device_id="AA:BB", kind="homepod",
                                   version="18.5") is False
    assert devices.get(db, "AA:BB").last_version == "18.5"


def test_list_excludes_retired_until_asked(db):
    devices.enroll(db, "A", "homepod", name="a")
    devices.enroll(db, "B", "esphome", name="b")
    assert {d.device_id for d in devices.list_devices(db)} == {"A", "B"}
    assert devices.retire(db, "A") is True
    assert {d.device_id for d in devices.list_devices(db)} == {"B"}
    assert {d.device_id for d in devices.list_devices(db, include_retired=True)} == {"A", "B"}
    assert devices.retire(db, "A") is False  # already retired
    # Retire logged an event.
    assert any(e.kind == "retired" for e in til.query(db))


def test_probe_persists_network_columns(db):
    probes.insert_probe(db, Probe(target_kind="homepod", target_id="hk",
                                  version="18.4", device_id="AA:BB", mac="AA:BB",
                                  ssid="home", ip="10.0.0.5", subnet="10.0.0.0/24"))
    row = db.execute("SELECT * FROM probes").fetchone()
    assert row["device_id"] == "AA:BB" and row["ssid"] == "home"
    assert row["ip"] == "10.0.0.5" and row["mac"] == "AA:BB"
