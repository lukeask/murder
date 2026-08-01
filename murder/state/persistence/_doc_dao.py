"""Shared parameterised doc-DAO backing notes and reports.

Notes and reports are structural twins (same columns, same revision shape).
Rather than duplicate the SQL, both bindings import this module and supply
three TRUSTED CONSTANTS — table name, revisions-table name, and the FK column
name used in the revisions table (e.g. ``note_name`` / ``report_name``).

These names are module-level constants in each binding, never sourced from
wire input.  f-string interpolation is safe here.
"""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from murder.state.persistence.connection import RepoDb


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Parameterised operations
# ---------------------------------------------------------------------------


def get_doc(db: RepoDb, table: str, name: str) -> dict[str, Any] | None:
    row = db.conn.execute(
        f"SELECT * FROM {table} WHERE repository_id = ? AND name = ?", (db.repository_id, name)
    ).fetchone()
    return dict(row) if row else None


def list_docs(db: RepoDb, table: str) -> list[dict[str, Any]]:
    """List active docs, projecting ``size`` (length of body) not raw body.

    Projection mirrors the notes.list_notes shape so callers consume ``size``
    rather than ``body`` — preserving the established contract.
    """
    rows = db.conn.execute(
        f"""
        SELECT id, name, created_at, updated_at, status, retired_at,
               materialized_path, length(body) AS size
          FROM {table}
         WHERE repository_id = ? AND status = 'active'
         ORDER BY updated_at DESC, name
        """,
        (db.repository_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_doc_name(db: RepoDb, table: str) -> str | None:
    row = db.conn.execute(
        f"SELECT name FROM {table} WHERE repository_id = ? AND status = 'active' ORDER BY updated_at DESC, name LIMIT 1",
        (db.repository_id,),
    ).fetchone()
    return str(row["name"]) if row else None


def upsert_doc(
    db: RepoDb,
    table: str,
    name: str,
    *,
    body: str,
    materialized_path: str,
) -> None:
    now = _now()
    existing = db.conn.execute(
        f"SELECT 1 FROM {table} WHERE repository_id = ? AND name = ?", (db.repository_id, name)
    ).fetchone()
    if existing is None:
        db.conn.execute(
            f"""
            INSERT INTO {table}
                (repository_id, id, name, created_at, updated_at, status, retired_at, body, materialized_path)
            VALUES (?, ?, ?, ?, ?, 'active', NULL, ?, ?)
            """,
            (db.repository_id, str(uuid4()), name, now, now, body, materialized_path),
        )
    else:
        db.conn.execute(
            f"""
            UPDATE {table}
               SET updated_at = ?, status = 'active', retired_at = NULL,
                   body = ?, materialized_path = ?
             WHERE repository_id = ? AND name = ?
            """,
            (now, body, materialized_path, db.repository_id, name),
        )


def rename_doc(
    db: RepoDb,
    table: str,
    revisions_table: str,
    fk_col: str,
    old_name: str,
    new_name: str,
    *,
    materialized_path: str,
) -> None:
    # Guard the destination key (table has ``name UNIQUE``) so a collision raises
    # a clear ValueError up front instead of an unguarded IntegrityError mid-
    # transaction. Matches rename_plan's contract. The check covers retired rows
    # too, since the unique index is not status-scoped.
    if (
        db.conn.execute(
            f"SELECT 1 FROM {table} WHERE repository_id = ? AND name = ?",
            (db.repository_id, new_name),
        ).fetchone()
        is not None
    ):
        raise ValueError(f"{table} already exists: {new_name}")
    now = _now()
    db.conn.execute("PRAGMA foreign_keys = OFF")
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        db.conn.execute(
            f"""
            UPDATE {table}
               SET name = ?, updated_at = ?, materialized_path = ?
             WHERE repository_id = ? AND name = ? AND status = 'active'
            """,
            (new_name, now, materialized_path, db.repository_id, old_name),
        )
        db.conn.execute(
            f"UPDATE {revisions_table} SET {fk_col} = ? WHERE repository_id = ? AND {fk_col} = ?",
            (new_name, db.repository_id, old_name),
        )
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
    finally:
        db.conn.execute("PRAGMA foreign_keys = ON")


def mark_doc_retired(
    db: RepoDb,
    table: str,
    name: str,
    *,
    materialized_path: str,
) -> None:
    now = _now()
    db.conn.execute(
        f"""
        UPDATE {table}
           SET status = 'retired', retired_at = ?, updated_at = ?,
               materialized_path = ?
         WHERE repository_id = ? AND name = ?
        """,
        (now, now, materialized_path, db.repository_id, name),
    )


def insert_revision(
    db: RepoDb,
    revisions_table: str,
    fk_col: str,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    cur = db.conn.execute(
        f"""
        INSERT INTO {revisions_table} (repository_id, {fk_col}, created_at, source, body, content_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (db.repository_id, name, _now(), source, body, content_hash),
    )
    return int(cur.lastrowid or 0)


def list_revisions(
    db: RepoDb,
    revisions_table: str,
    fk_col: str,
    name: str,
) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        f"""
        SELECT id, {fk_col}, created_at, source, body, content_hash
          FROM {revisions_table}
         WHERE repository_id = ? AND {fk_col} = ?
         ORDER BY id
        """,
        (db.repository_id, name),
    ).fetchall()
    return [dict(r) for r in rows]
