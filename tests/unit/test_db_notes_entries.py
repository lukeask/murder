"""`notes_entries` + `notetaker_context` tables and DB helpers."""

from __future__ import annotations

import sqlite3

from murder import db as dbmod
from murder.db import (
    NOTETAKER_CONTEXT_MATERIALIZED_REL,
    NOTETAKER_CONTEXT_ROW_ID,
    init_schema,
)


def test_notetaker_context_singleton_bootstrapped(memdb: sqlite3.Connection) -> None:
    row = dbmod.get_notetaker_context(memdb)
    assert row is not None
    assert row["id"] == NOTETAKER_CONTEXT_ROW_ID
    assert row["body"] == ""
    assert row["materialized_path"] == NOTETAKER_CONTEXT_MATERIALIZED_REL


def test_init_schema_twice_keeps_one_notetaker_context_row(memdb: sqlite3.Connection) -> None:
    init_schema(memdb)
    init_schema(memdb)
    n = memdb.execute("SELECT COUNT(*) AS n FROM notetaker_context").fetchone()["n"]
    assert int(n) == 1


def test_insert_notes_entry_returns_row_id(memdb: sqlite3.Connection) -> None:
    eid = dbmod.insert_notes_entry(memdb, raw="a", cleaned="b", short_vers="c")
    assert eid >= 1


def test_list_recent_notes_entries_orders_newest_first(memdb: sqlite3.Connection) -> None:
    first = dbmod.insert_notes_entry(memdb, raw="1", cleaned="1", short_vers="1")
    second = dbmod.insert_notes_entry(memdb, raw="2", cleaned="2", short_vers="2")
    rows = dbmod.list_recent_notes_entries(memdb, limit=10)
    assert [r["id"] for r in rows] == [second, first]
    assert rows[0]["short_vers"] == "2"


def test_upsert_notetaker_context_updates_body(memdb: sqlite3.Connection) -> None:
    dbmod.upsert_notetaker_context(
        memdb,
        body="hello",
        materialized_path=".murder/notetakercontext.md",
    )
    row = dbmod.get_notetaker_context(memdb)
    assert row is not None
    assert row["body"] == "hello"


def test_list_notes_orders_active_by_updated_at(memdb: sqlite3.Connection) -> None:
    dbmod.upsert_note(
        memdb,
        "older",
        body="old",
        materialized_path=".murder/notes/older.md",
    )
    dbmod.upsert_note(
        memdb,
        "newer",
        body="new",
        materialized_path=".murder/notes/newer.md",
    )
    memdb.execute("UPDATE notes SET updated_at = '2026-01-01T00:00:00' WHERE name = 'older'")
    memdb.execute("UPDATE notes SET updated_at = '2026-01-02T00:00:00' WHERE name = 'newer'")

    assert [row["name"] for row in dbmod.list_notes(memdb)] == ["newer", "older"]
    assert dbmod.latest_note_name(memdb) == "newer"


def test_notes_have_uuid_identity(memdb: sqlite3.Connection) -> None:
    dbmod.upsert_note(memdb, "n", body="body", materialized_path=".murder/notes/n.md")
    row = dbmod.get_note(memdb, "n")
    assert row is not None
    assert row["id"]
    assert row["status"] == "active"


def test_init_schema_migrates_legacy_notes_without_status() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE notes (
            name              TEXT PRIMARY KEY,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            body              TEXT NOT NULL DEFAULT '',
            materialized_path TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO notes (name, created_at, updated_at, body, materialized_path)
        VALUES ('legacy', '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                'body', '.murder/notes/legacy.md')
        """
    )
    try:
        init_schema(conn)
        row = dbmod.get_note(conn, "legacy")
        assert row is not None
        assert row["id"]
        assert row["status"] == "active"
    finally:
        conn.close()
