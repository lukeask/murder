"""Persistence for notetaker_context singleton and notes_entries captures."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime
from typing import Any

from murder.state.persistence.connection import RepoDb
from murder.state.storage.paths import MURDER_DIR_NAME

NOTETAKER_CONTEXT_MATERIALIZED_REL = f"{MURDER_DIR_NAME}/notetakercontext.md"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def ensure_notetaker_context_row(db: RepoDb) -> None:
    """Make sure singleton row id=1 exists (survives repeated init_db)."""
    db.conn.execute(
        """
        INSERT OR IGNORE INTO notetaker_context (repository_id, body, updated_at, materialized_path)
        VALUES (?, '', ?, ?)
        """,
        (db.repository_id, _now(), NOTETAKER_CONTEXT_MATERIALIZED_REL),
    )


def get_notetaker_context(db: RepoDb) -> dict[str, Any] | None:
    row = db.conn.execute(
        "SELECT * FROM notetaker_context WHERE repository_id = ?",
        (db.repository_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_notetaker_context(db: RepoDb, *, body: str, materialized_path: str) -> None:
    db.conn.execute(
        """
        UPDATE notetaker_context
           SET body = ?, updated_at = ?, materialized_path = ?
         WHERE repository_id = ?
        """,
        (body, _now(), materialized_path, db.repository_id),
    )


def insert_notes_entry(db: RepoDb, *, raw: str, cleaned: str, short_vers: str) -> int:
    cur = db.conn.execute(
        """
        INSERT INTO notes_entries (repository_id, ts, raw, cleaned, short_vers)
        VALUES (?, ?, ?, ?, ?)
        """,
        (db.repository_id, _now(), raw, cleaned, short_vers),
    )
    return int(cur.lastrowid or 0)


def update_notes_entry_short_vers(db: RepoDb, entry_id: int, short_vers: str) -> None:
    db.conn.execute(
        "UPDATE notes_entries SET short_vers = ? WHERE repository_id = ? AND id = ?",
        (short_vers, db.repository_id, entry_id),
    )


def list_recent_notes_entries(db: RepoDb, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT id, ts, raw, cleaned, short_vers
          FROM notes_entries
         WHERE repository_id = ?
         ORDER BY ts DESC, id DESC
         LIMIT ?
        """,
        (db.repository_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
