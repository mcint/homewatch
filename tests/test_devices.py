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


def test_rename_sets_display_name_separate_from_detected(db):
    devices.enroll(db, "AA:BB", "homepod", name="Bedroom")  # detected name
    assert devices.rename(db, "AA:BB", "homepod-bedroom") is True
    dev = devices.get(db, "AA:BB")
    assert dev.name == "Bedroom" and dev.display_name == "homepod-bedroom"
    assert devices.display(dev) == "homepod-bedroom"  # display_name wins
    # Re-sighting must not clobber the chosen display name.
    devices.record_sighting(db, device_id="AA:BB", kind="homepod", name="Bedroom",
                            version="26.5")
    assert devices.get(db, "AA:BB").display_name == "homepod-bedroom"
    # Clearing reverts to detected.
    devices.rename(db, "AA:BB", None)
    assert devices.display(devices.get(db, "AA:BB")) == "Bedroom"
    assert devices.rename(db, "nope", "x") is False


def test_observe_homepod_auto_enrolls(db):
    p = Probe(target_kind="homepod", target_id="HP-ID", version="18.4",
              extra={"name": "homepod-kitchen", "deviceid": "AA:BB"})
    pid = probes.observe(db, p, ssid="home", subnet="10.0.0.0/24")
    assert pid > 0
    dev = devices.get(db, "HP-ID")
    assert dev.kind == "homepod" and dev.product == "homepod_software"
    assert dev.last_version == "18.4" and dev.name == "homepod-kitchen"
    assert dev.ssid == "home" and dev.identifiers.get("mac") == "AA:BB"
    row = db.execute("SELECT * FROM probes").fetchone()
    assert row["device_id"] == "HP-ID" and row["mac"] == "AA:BB"


def test_observe_ha_derives_product_from_install_type(db):
    p = Probe(target_kind="home_assistant", target_id="http://hass:8123",
              version="2026.4.3", extra={"installation_type": "Home Assistant OS"})
    probes.observe(db, p)
    dev = devices.get(db, "http://hass:8123")
    assert dev.kind == "home_assistant" and dev.product == "home_assistant_os"


def test_observe_failed_probe_records_but_does_not_enroll(db):
    p = Probe(target_kind="home_assistant", target_id="http://hass:8123",
              error="HTTP 401")
    probes.observe(db, p)
    assert devices.get(db, "http://hass:8123") is None  # failure ≠ sighting
    assert db.execute("SELECT COUNT(*) c FROM probes").fetchone()["c"] == 1


def test_probe_persists_network_columns(db):
    probes.insert_probe(db, Probe(target_kind="homepod", target_id="hk",
                                  version="18.4", device_id="AA:BB", mac="AA:BB",
                                  ssid="home", ip="10.0.0.5", subnet="10.0.0.0/24"))
    row = db.execute("SELECT * FROM probes").fetchone()
    assert row["device_id"] == "AA:BB" and row["ssid"] == "home"
    assert row["ip"] == "10.0.0.5" and row["mac"] == "AA:BB"
