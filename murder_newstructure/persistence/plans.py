"""Persistence for the plans and plan_revisions tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from murder.plans.schema import Plan


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_plan_revision(
    conn: sqlite3.Connection,
    plan: Plan,
    *,
    source: str,
    content_hash: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO plan_revisions
            (plan_name, created_at, source, status, body, frontmatter_json, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.name,
            _now(),
            source,
            plan.status.value,
            plan.body,
            json.dumps(plan.frontmatter, sort_keys=True, default=str),
            content_hash,
        ),
    )
    conn.execute(
        "UPDATE plans SET revision_count = revision_count + 1 WHERE name = ?",
        (plan.name,),
    )
    return int(cur.lastrowid or 0)


def upsert_plan(
    conn: sqlite3.Connection,
    plan: Plan,
    *,
    content_hash: str,
    materialized_path: str,
    file_hash: str | None = None,
    last_materialized_hash: str | None = None,
    sync_state: str = "synced",
    conflict_reason: str | None = None,
    parse_error: str | None = None,
    create_revision: bool = True,
    revision_source: str = "db",
) -> None:
    now = _now()
    existing = conn.execute(
        "SELECT revision_count FROM plans WHERE name = ?", (plan.name,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO plans
                (name, status, created_at, updated_at, body, frontmatter_json,
                 body_hash, file_hash, last_materialized_hash, materialized_path,
                 sync_state, conflict_reason, parse_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.name,
                plan.status.value,
                plan.created_at.isoformat(timespec="seconds"),
                plan.updated_at.isoformat(timespec="seconds") if plan.updated_at else now,
                plan.body,
                json.dumps(plan.frontmatter, sort_keys=True, default=str),
                content_hash,
                file_hash,
                last_materialized_hash,
                materialized_path,
                sync_state,
                conflict_reason,
                parse_error,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE plans
               SET status = ?, updated_at = ?, body = ?, frontmatter_json = ?,
                   body_hash = ?, file_hash = ?, last_materialized_hash = ?,
                   materialized_path = ?, sync_state = ?, conflict_reason = ?,
                   parse_error = ?
             WHERE name = ?
            """,
            (
                plan.status.value,
                plan.updated_at.isoformat(timespec="seconds") if plan.updated_at else now,
                plan.body,
                json.dumps(plan.frontmatter, sort_keys=True, default=str),
                content_hash,
                file_hash,
                last_materialized_hash,
                materialized_path,
                sync_state,
                conflict_reason,
                parse_error,
                plan.name,
            ),
        )
    conn.execute("DELETE FROM plan_related_tickets WHERE plan_name = ?", (plan.name,))
    for ticket_id in plan.related_tickets:
        conn.execute(
            """
            INSERT OR IGNORE INTO plan_related_tickets(plan_name, ticket_id)
            VALUES (?, ?)
            """,
            (plan.name, ticket_id),
        )
    if create_revision:
        insert_plan_revision(conn, plan, source=revision_source, content_hash=content_hash)


def list_plans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM plan_revisions r WHERE r.plan_name = p.name) AS revisions
          FROM plans p
         ORDER BY p.updated_at DESC, p.name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_plan_row(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM plans WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def mark_plan_sync_state(
    conn: sqlite3.Connection,
    name: str,
    sync_state: str,
    *,
    file_hash: str | None = None,
    conflict_reason: str | None = None,
    parse_error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE plans
           SET sync_state = ?, file_hash = COALESCE(?, file_hash),
               conflict_reason = ?, parse_error = ?, updated_at = ?
         WHERE name = ?
        """,
        (sync_state, file_hash, conflict_reason, parse_error, _now(), name),
    )
