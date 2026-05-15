"""SCHEMA_SQL applies cleanly; FKs and CHECKs do their job."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from murder.db import SCHEMA_SQL, init_schema
from murder.storage.paths import ticket_yaml


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


def test_tickets_metadata_columns_and_indexes_present(memdb: sqlite3.Connection) -> None:
    columns = {r["name"] for r in memdb.execute("PRAGMA table_info(tickets)").fetchall()}
    assert "schedule_at" in columns
    assert "metadata_hash" in columns
    assert "metadata_file_hash" in columns
    assert "metadata_last_materialized_hash" in columns
    assert "metadata_materialized_path" in columns
    assert "metadata_sync_state" in columns
    assert "metadata_parse_error" in columns
    assert "metadata_conflict_reason" in columns

    indexes = {r["name"] for r in memdb.execute("PRAGMA index_list(tickets)").fetchall()}
    assert "idx_tickets_schedule_at" in indexes
    assert "idx_tickets_metadata_sync_state" in indexes


def test_ticket_metadata_columns_migrate_existing_tickets_table() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("PRAGMA foreign_keys = ON;")
        conn.executescript(
            """
            CREATE TABLE tickets (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                wave          INTEGER NOT NULL,
                status        TEXT NOT NULL CHECK (status IN
                              ('planned','ready','in_progress','blocked','done','failed')),
                harness       TEXT,
                model         TEXT,
                attempts      INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );
            """
        )
        init_schema(conn)
        init_schema(conn)

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()}
        assert "schedule_at" in cols
        assert "metadata_sync_state" in cols

        conn.execute(
            """
            INSERT INTO tickets(
                id, title, wave, status, harness, model, schedule_at,
                metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
                metadata_materialized_path, metadata_sync_state, metadata_parse_error,
                metadata_conflict_reason, attempts, created_at, updated_at
            )
            VALUES (
                'T-1', 'Ticket', 1, 'planned', NULL, NULL, NULL,
                NULL, NULL, NULL, ?, 'synced', NULL, NULL, 0,
                '2026-01-01T00:00:00', '2026-01-01T00:00:00'
            )
            """,
            (".murder/tickets/T-1.yaml",),
        )
        row = conn.execute(
            "SELECT metadata_sync_state FROM tickets WHERE id = 'T-1'"
        ).fetchone()
        assert row["metadata_sync_state"] == "synced"
    finally:
        conn.close()


def test_ticket_yaml_path_is_flat() -> None:
    assert ticket_yaml(Path("/repo"), "t007") == Path("/repo/.murder/tickets/t007.yaml")
