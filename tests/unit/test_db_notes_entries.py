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
