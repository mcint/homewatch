"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3

import pytest

from homewatch.db import get_db


@pytest.fixture
def db(tmp_path) -> sqlite3.Connection:
    """A fresh migrated SQLite DB in a temp dir."""
    conn = get_db(tmp_path / "test.sqlite")
    yield conn
    conn.close()
