"""Persistence for the notes and note_revisions tables.

Thin binding over ``_doc_dao`` — all public function names and signatures are
preserved so callers are unaffected.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from murder.state.persistence._doc_dao import (
    get_doc,
    insert_revision,
    latest_doc_name,
    list_docs,
    list_revisions,
    mark_doc_retired,
    rename_doc,
    upsert_doc,
)

# Trusted constants — never wire input.
_TABLE = "notes"
_REVISIONS_TABLE = "note_revisions"
_FK_COL = "note_name"


def get_note(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    return get_doc(conn, _TABLE, name)


def list_notes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return list_docs(conn, _TABLE)


def latest_note_name(conn: sqlite3.Connection) -> str | None:
    return latest_doc_name(conn, _TABLE)


def upsert_note(conn: sqlite3.Connection, name: str, *, body: str, materialized_path: str) -> None:
    upsert_doc(conn, _TABLE, name, body=body, materialized_path=materialized_path)


def rename_note(
    conn: sqlite3.Connection, old_name: str, new_name: str, *, materialized_path: str
) -> None:
    rename_doc(conn, _TABLE, _REVISIONS_TABLE, _FK_COL, old_name, new_name, materialized_path=materialized_path)


def mark_note_retired(conn: sqlite3.Connection, name: str, *, materialized_path: str) -> None:
    mark_doc_retired(conn, _TABLE, name, materialized_path=materialized_path)


def insert_note_revision(
    conn: sqlite3.Connection,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    return insert_revision(conn, _REVISIONS_TABLE, _FK_COL, name, source=source, body=body, content_hash=content_hash)


def list_note_revisions(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    return list_revisions(conn, _REVISIONS_TABLE, _FK_COL, name)
