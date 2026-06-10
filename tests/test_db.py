"""DB migration + bootstrap behaviour."""

from __future__ import annotations

from homewatch.db import get_db, migrate


def test_migrations_create_all_tables(db):
    names = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"releases", "probes", "til_events", "source_state", "schema_version"} <= names


def test_wal_mode_enabled(db):
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_schema_version_recorded(db):
    versions = {r["version"] for r in db.execute("SELECT version FROM schema_version")}
    assert 1 in versions


def test_migrate_is_idempotent(db):
    # Second run applies nothing new.
    assert migrate(db) == []


def test_reopen_does_not_remigrate(tmp_path):
    path = tmp_path / "x.sqlite"
    get_db(path).close()
    conn = get_db(path)
    try:
        assert migrate(conn) == []
    finally:
        conn.close()
