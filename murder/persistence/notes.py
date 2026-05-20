"""Persistence for the notes and note_revisions tables."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def get_note(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM notes WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_notes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, name, created_at, updated_at, status, retired_at,
               materialized_path, length(body) AS size
          FROM notes
         WHERE status = 'active'
         ORDER BY updated_at DESC, name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def latest_note_name(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT name FROM notes WHERE status = 'active' ORDER BY updated_at DESC, name LIMIT 1"
    ).fetchone()
    return str(row["name"]) if row else None


def upsert_note(conn: sqlite3.Connection, name: str, *, body: str, materialized_path: str) -> None:
    now = _now()
    existing = conn.execute("SELECT 1 FROM notes WHERE name = ?", (name,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO notes
                (id, name, created_at, updated_at, status, retired_at, body, materialized_path)
            VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)
            """,
            (str(uuid4()), name, now, now, body, materialized_path),
        )
    else:
        conn.execute(
            """
            UPDATE notes
               SET updated_at = ?, status = 'active', retired_at = NULL,
                   body = ?, materialized_path = ?
             WHERE name = ?
            """,
            (now, body, materialized_path, name),
        )


def rename_note(
    conn: sqlite3.Connection, old_name: str, new_name: str, *, materialized_path: str
) -> None:
    now = _now()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            UPDATE notes
               SET name = ?, updated_at = ?, materialized_path = ?
             WHERE name = ? AND status = 'active'
            """,
            (new_name, now, materialized_path, old_name),
        )
        conn.execute(
            "UPDATE note_revisions SET note_name = ? WHERE note_name = ?",
            (new_name, old_name),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def mark_note_retired(conn: sqlite3.Connection, name: str, *, materialized_path: str) -> None:
    now = _now()
    conn.execute(
        """
        UPDATE notes
           SET status = 'retired', retired_at = ?, updated_at = ?,
               materialized_path = ?
         WHERE name = ?
        """,
        (now, now, materialized_path, name),
    )


def insert_note_revision(
    conn: sqlite3.Connection,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO note_revisions (note_name, created_at, source, body, content_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, _now(), source, body, content_hash),
    )
    return int(cur.lastrowid or 0)


def list_note_revisions(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, note_name, created_at, source, body, content_hash
          FROM note_revisions
         WHERE note_name = ?
         ORDER BY id
        """,
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]
