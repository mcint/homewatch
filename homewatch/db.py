"""SQLite connection + numbered-migration runner. See spec §2.

WAL mode, one DB file. Migrations are numbered ``NNN_*.sql`` files in
``homewatch/migrations/``; they are applied in order and recorded in a
``schema_version`` table that is bootstrapped on first run.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> str:
    """ISO-8601 UTC timestamp with trailing ``Z``, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a WAL-mode connection with row access by name and FK enforcement."""
    db_path = Path(db_path)
    if db_path.parent and str(db_path.parent) not in ("", "."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync deps in a threadpool and may
    # hand a connection to a different thread than it was created on. Each
    # request gets its own short-lived connection, so there's no shared-state race.
    conn = sqlite3.connect(
        db_path, isolation_level=None, check_same_thread=False
    )  # autocommit; we manage txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "  version    INTEGER PRIMARY KEY,"
        "  applied_at TEXT NOT NULL"
        ")"
    )


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    return {r["version"] for r in rows}


def _migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        num = int(path.name.split("_", 1)[0])
        out.append((num, path))
    return out


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply any pending migrations. Returns the list of versions applied now."""
    _ensure_version_table(conn)
    done = _applied_versions(conn)
    applied: list[int] = []
    for num, path in _migration_files():
        if num in done:
            continue
        sql = path.read_text(encoding="utf-8")
        # executescript() wraps its own transaction (and implicitly commits any
        # pending one), so we don't manage BEGIN/COMMIT here. If the DDL fails it
        # raises and the version row is never written, so the migration re-runs.
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (num, utcnow()),
        )
        applied.append(num)
    return applied


def migration_status(conn: sqlite3.Connection) -> list[tuple[int, bool]]:
    """[(version, applied?), …] across all migration files, in order."""
    _ensure_version_table(conn)
    done = _applied_versions(conn)
    return [(num, num in done) for num, _ in _migration_files()]


def get_db(db_path: Path | str) -> sqlite3.Connection:
    """Connect and ensure the schema is current (bootstrap on first run)."""
    conn = connect(db_path)
    migrate(conn)
    return conn


def checkpoint(conn: sqlite3.Connection) -> None:
    """Truncate the WAL — call on clean shutdown so the .sqlite is self-contained."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
