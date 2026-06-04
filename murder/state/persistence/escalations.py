"""Persistence for the escalations table."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from murder.state.persistence.records import EscalationRecord, escalation_record_from_row


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_escalation(
    conn: sqlite3.Connection,
    *,
    ticket_id: str | None,
    severity: int,
    reason: str,
    to_recipient: str,
    source_event_id: int | None = None,
    body_path: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO escalations
            (ts, ticket_id, severity, reason, to_recipient, source_event_id, body_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_now(), ticket_id, severity, reason, to_recipient, source_event_id, body_path),
    )
    return int(cur.lastrowid or 0)


def list_pending_escalations(
    conn: sqlite3.Connection, recipient: str | None = None
) -> list[EscalationRecord]:
    if recipient is None:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 ORDER BY ts DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 AND to_recipient = ? ORDER BY ts DESC",
            (recipient,),
        ).fetchall()
    return [escalation_record_from_row(r) for r in rows]


def resolve_escalation(conn: sqlite3.Connection, escalation_id: int) -> None:
    conn.execute(
        "UPDATE escalations SET resolved = 1, resolved_at = ? WHERE id = ?",
        (_now(), escalation_id),
    )
