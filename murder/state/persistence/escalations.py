"""Persistence for the escalations table."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime

from murder.roster.repository import RosterRepository
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.records import EscalationRecord, escalation_record_from_row


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_escalation(
    db: RepoDb,
    *,
    ticket_id: str | None,
    severity: int,
    reason: str,
    to_recipient: str,
    source_event_id: int | None = None,
    body_path: str | None = None,
) -> int:
    conn = db.conn
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO escalations
                (repository_id, ts, ticket_id, severity, reason, to_recipient, source_event_id, body_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db.repository_id,
                _now(),
                ticket_id,
                severity,
                reason,
                to_recipient,
                source_event_id,
                body_path,
            ),
        )
        escalation_id = int(cur.lastrowid or 0)
        RosterRepository().invalidate(db, subject_key=f"escalation:{escalation_id}")
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()
    return escalation_id


def list_pending_escalations(db: RepoDb, recipient: str | None = None) -> list[EscalationRecord]:
    if recipient is None:
        rows = db.conn.execute(
            "SELECT * FROM escalations WHERE repository_id = ? AND resolved = 0 ORDER BY ts DESC",
            (db.repository_id,),
        ).fetchall()
    else:
        rows = db.conn.execute(
            "SELECT * FROM escalations WHERE repository_id = ? AND resolved = 0 AND to_recipient = ? ORDER BY ts DESC",
            (db.repository_id, recipient),
        ).fetchall()
    return [escalation_record_from_row(r) for r in rows]


def resolve_escalation(db: RepoDb, escalation_id: int) -> None:
    conn = db.conn
    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE escalations SET resolved = 1, resolved_at = ? WHERE repository_id = ? AND id = ?",
            (_now(), db.repository_id, escalation_id),
        )
        RosterRepository().invalidate(db, subject_key=f"escalation:{escalation_id}")
    except BaseException:
        if owns_transaction:
            conn.rollback()
        raise
    else:
        if owns_transaction:
            conn.commit()
