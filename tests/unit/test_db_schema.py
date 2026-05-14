"""SCHEMA_SQL applies cleanly; FKs and CHECKs do their job."""

from __future__ import annotations

import sqlite3

import pytest

from murder.db import SCHEMA_SQL, init_schema


def test_schema_idempotent() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        conn.executescript("PRAGMA foreign_keys = ON;")
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


def test_status_check_constraint_rejects_garbage(memdb: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        memdb.execute(
            """
            INSERT INTO tickets(id, title, wave, status, harness, model, attempts, created_at, updated_at)
            VALUES ('T-1', 'Bad Status', 1, 'nonsense', NULL, NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
            """
        )


def test_ticket_dep_self_loop_rejected(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, model, attempts, created_at, updated_at)
        VALUES ('T-1', 'One', 1, 'planned', NULL, NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        memdb.execute(
            "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('T-1', 'T-1')"
        )


def test_cascade_deletes(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, model, attempts, created_at, updated_at)
        VALUES ('T-1', 'One', 1, 'planned', NULL, NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """
    )
    memdb.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, model, attempts, created_at, updated_at)
        VALUES ('T-2', 'Two', 1, 'planned', NULL, NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """
    )
    memdb.execute("INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('T-2', 'T-1')")
    memdb.execute("INSERT INTO ticket_write_set(ticket_id, path) VALUES ('T-1', 'murder/db.py')")
    memdb.execute("INSERT INTO ticket_skills(ticket_id, skill) VALUES ('T-1', 'db')")
    memdb.execute("INSERT INTO checklist(ticket_id, ord, text, done) VALUES ('T-1', 0, 'x', 0)")
    memdb.execute("DELETE FROM tickets WHERE id = 'T-1'")

    assert memdb.execute("SELECT COUNT(*) AS n FROM ticket_deps").fetchone()["n"] == 0
    assert memdb.execute("SELECT COUNT(*) AS n FROM ticket_write_set").fetchone()["n"] == 0
    assert memdb.execute("SELECT COUNT(*) AS n FROM ticket_skills").fetchone()["n"] == 0
    assert memdb.execute("SELECT COUNT(*) AS n FROM checklist").fetchone()["n"] == 0


def test_events_schema_version_column_migrates_existing_table() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("PRAGMA foreign_keys = ON;")
        conn.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                config_snapshot TEXT NOT NULL
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                agent_id TEXT,
                role TEXT,
                ticket_id TEXT,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        init_schema(conn)
        names = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        assert "schema_version" in names
    finally:
        conn.close()
