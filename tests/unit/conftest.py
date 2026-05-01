"""Unit-test fixtures: in-memory SQLite, sample tickets, no IO."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from murder.db import init_schema


@pytest.fixture
def memdb() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory SQLite with the murder schema applied."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript("PRAGMA foreign_keys = ON;")
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()
