"""Persistence for notetaker_context singleton and notes_entries captures."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from murder.state.storage.paths import MURDER_DIR_NAME

NOTETAKER_CONTEXT_ROW_ID = 1
NOTETAKER_CONTEXT_MATERIALIZED_REL = f"{MURDER_DIR_NAME}/notetakercontext.md"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def ensure_notetaker_context_row(conn: sqlite3.Connection) -> None:
    """Ensure singleton row id=1 exists (survives repeated init_db)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO notetaker_context (id, body, updated_at, materialized_path)
        VALUES (?, '', ?, ?)
        """,
        (NOTETAKER_CONTEXT_ROW_ID, _now(), NOTETAKER_CONTEXT_MATERIALIZED_REL),
    )


def get_notetaker_context(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM notetaker_context WHERE id = ?",
        (NOTETAKER_CONTEXT_ROW_ID,),
    ).fetchone()
    return dict(row) if row else None


def upsert_notetaker_context(
    conn: sqlite3.Connection, *, body: str, materialized_path: str
) -> None:
    conn.execute(
        """
        UPDATE notetaker_context
           SET body = ?, updated_at = ?, materialized_path = ?
         WHERE id = ?
        """,
        (body, _now(), materialized_path, NOTETAKER_CONTEXT_ROW_ID),
    )


def insert_notes_entry(conn: sqlite3.Connection, *, raw: str, cleaned: str, short_vers: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO notes_entries (ts, raw, cleaned, short_vers)
        VALUES (?, ?, ?, ?)
        """,
        (_now(), raw, cleaned, short_vers),
    )
    return int(cur.lastrowid or 0)


def update_notes_entry_short_vers(conn: sqlite3.Connection, entry_id: int, short_vers: str) -> None:
    conn.execute(
        "UPDATE notes_entries SET short_vers = ? WHERE id = ?",
        (short_vers, entry_id),
    )


def list_recent_notes_entries(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, ts, raw, cleaned, short_vers
          FROM notes_entries
         ORDER BY ts DESC, id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
