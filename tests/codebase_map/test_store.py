"""Tests for the codebase-map DB store + schema migration (t060)."""

from __future__ import annotations

import sqlite3

from murder.codebase_map.store import (
    latest_map_sha,
    load_summary,
    rows_for_commit,
    snapshot_file,
    snapshot_rollup,
)
from murder.codebase_map.summarize import FileSummary
from murder.state.persistence.migrations import _migrate_map_summaries
from murder.state.persistence.schema import SCHEMA_SQL


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _summary(path: str, body: str = "# body") -> FileSummary:
    return FileSummary(
        path=path,
        body=body,
        source_hash="b" * 64,
        source_tokens=200,
        summary_tokens=10,
    )


def test_snapshot_file_round_trips():
    db = _db()
    snapshot_file(db, "pkg/a.py", "sha1", _summary("pkg/a.py", body="hello"))
    row = load_summary(db, "pkg/a.py", "sha1")
    assert row is not None
    assert row["body"] == "hello"
    assert row["kind"] == "file"
    assert row["source_hash"] == "b" * 64
    assert row["source_tokens"] == 200
    assert row["summary_tokens"] == 10


def test_snapshot_file_upserts_not_duplicates():
    db = _db()
    snapshot_file(db, "pkg/a.py", "sha1", _summary("pkg/a.py", body="v1"))
    snapshot_file(db, "pkg/a.py", "sha1", _summary("pkg/a.py", body="v2"))
    rows = rows_for_commit(db, "sha1")
    assert len(rows) == 1
    assert rows[0]["body"] == "v2"


def test_snapshot_rollup_nulls_source_fields():
    db = _db()
    snapshot_rollup(db, "pkg", "sha1", "dir", "dir body", summary_tokens=7)
    row = load_summary(db, "pkg", "sha1")
    assert row["kind"] == "dir"
    assert row["source_hash"] is None
    assert row["source_tokens"] is None
    assert row["summary_tokens"] == 7
    assert row["body"] == "dir body"


def test_load_summary_missing_returns_none():
    db = _db()
    assert load_summary(db, "nope.py", "sha1") is None


def test_latest_map_sha_returns_most_recent():
    db = _db()
    snapshot_file(db, "a.py", "old_sha", _summary("a.py"))
    snapshot_file(db, "a.py", "new_sha", _summary("a.py"))
    # new_sha was generated last → it is the latest.
    assert latest_map_sha(db) == "new_sha"


def test_latest_map_sha_empty_is_none():
    db = _db()
    assert latest_map_sha(db) is None


def test_rows_for_commit_returns_all():
    db = _db()
    snapshot_file(db, "a.py", "sha1", _summary("a.py"))
    snapshot_file(db, "b.py", "sha1", _summary("b.py"))
    snapshot_rollup(db, "ROOT", "sha1", "root", "root body", summary_tokens=3)
    snapshot_file(db, "c.py", "sha2", _summary("c.py"))

    rows = rows_for_commit(db, "sha1")
    assert {r["path"] for r in rows} == {"a.py", "b.py", "ROOT"}
    assert len(rows_for_commit(db, "sha2")) == 1


def test_migration_creates_table_and_is_idempotent():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # A DB lacking the table.
    present = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "map_summaries" not in present

    _migrate_map_summaries(conn)
    present = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "map_summaries" in present

    # Idempotent re-run: no error, table still present, round-trips.
    _migrate_map_summaries(conn)
    snapshot_file(conn, "a.py", "sha1", _summary("a.py"))
    assert load_summary(conn, "a.py", "sha1") is not None
