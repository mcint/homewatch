"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sqlite3

import pytest

# Some shells on this machine export a stale SSL_CERT_FILE (a removed zerobrew
# path); Python's ssl honors it and httpx fails to build its default context.
# Repair it to the installed certifi bundle so the suite runs anywhere.
if not os.path.exists(os.environ.get("SSL_CERT_FILE", "")):
    import certifi

    os.environ["SSL_CERT_FILE"] = certifi.where()

from homewatch.db import get_db


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """A fresh migrated SQLite DB in a temp dir."""
    conn = get_db(tmp_path / "test.sqlite")
    yield conn
    conn.close()
