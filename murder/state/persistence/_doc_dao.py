"""Shared parameterised doc-DAO backing notes and reports.

Notes and reports are structural twins (same columns, same revision shape).
Rather than duplicate the SQL, both bindings import this module and supply
three TRUSTED CONSTANTS — table name, revisions-table name, and the FK column
name used in the revisions table (e.g. ``note_name`` / ``report_name``).

These names are module-level constants in each binding, never sourced from
wire input.  f-string interpolation is safe here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Parameterised operations
# ---------------------------------------------------------------------------


def get_doc(conn: sqlite3.Connection, table: str, name: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_docs(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """List active docs, projecting ``size`` (length of body) not raw body.

    Projection mirrors the notes.list_notes shape so callers consume ``size``
    rather than ``body`` — preserving the established contract.
    """
    rows = conn.execute(
        f"""
        SELECT id, name, created_at, updated_at, status, retired_at,
               materialized_path, length(body) AS size
          FROM {table}
         WHERE status = 'active'
         ORDER BY updated_at DESC, name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def latest_doc_name(conn: sqlite3.Connection, table: str) -> str | None:
    row = conn.execute(
        f"SELECT name FROM {table} WHERE status = 'active' ORDER BY updated_at DESC, name LIMIT 1"
    ).fetchone()
    return str(row["name"]) if row else None


def upsert_doc(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    *,
    body: str,
    materialized_path: str,
) -> None:
    now = _now()
    existing = conn.execute(f"SELECT 1 FROM {table} WHERE name = ?", (name,)).fetchone()
    if existing is None:
        conn.execute(
            f"""
            INSERT INTO {table}
                (id, name, created_at, updated_at, status, retired_at, body, materialized_path)
            VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)
            """,
            (str(uuid4()), name, now, now, body, materialized_path),
        )
    else:
        conn.execute(
            f"""
            UPDATE {table}
               SET updated_at = ?, status = 'active', retired_at = NULL,
                   body = ?, materialized_path = ?
             WHERE name = ?
            """,
            (now, body, materialized_path, name),
        )


def rename_doc(
    conn: sqlite3.Connection,
    table: str,
    revisions_table: str,
    fk_col: str,
    old_name: str,
    new_name: str,
    *,
    materialized_path: str,
) -> None:
    now = _now()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        conn.execute(
            f"""
            UPDATE {table}
               SET name = ?, updated_at = ?, materialized_path = ?
             WHERE name = ? AND status = 'active'
            """,
            (new_name, now, materialized_path, old_name),
        )
        conn.execute(
            f"UPDATE {revisions_table} SET {fk_col} = ? WHERE {fk_col} = ?",
            (new_name, old_name),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def mark_doc_retired(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    *,
    materialized_path: str,
) -> None:
    now = _now()
    conn.execute(
        f"""
        UPDATE {table}
           SET status = 'retired', retired_at = ?, updated_at = ?,
               materialized_path = ?
         WHERE name = ?
        """,
        (now, now, materialized_path, name),
    )


def insert_revision(
    conn: sqlite3.Connection,
    revisions_table: str,
    fk_col: str,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    cur = conn.execute(
        f"""
        INSERT INTO {revisions_table} ({fk_col}, created_at, source, body, content_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, _now(), source, body, content_hash),
    )
    return int(cur.lastrowid or 0)


def list_revisions(
    conn: sqlite3.Connection,
    revisions_table: str,
    fk_col: str,
    name: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT id, {fk_col}, created_at, source, body, content_hash
          FROM {revisions_table}
         WHERE {fk_col} = ?
         ORDER BY id
        """,
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]
