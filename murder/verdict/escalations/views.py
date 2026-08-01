"""SQLite read/write projections for the escalations table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from murder.state.persistence.connection import RepoDb


@dataclass(frozen=True, slots=True)
class EscalationRecord:
    id: int
    created_at: datetime
    ticket_id: str | None
    severity: int
    reason: str
    to_recipient: str
    body_path: str | None
    resolved_at: datetime | None
    source_event_id: int | None


def get_active_escalations(db: RepoDb) -> tuple[EscalationRecord, ...]:
    """Return unresolved escalations (``resolved_at IS NULL``), newest first."""
    rows = db.conn.execute(
            """
            SELECT id, ts, ticket_id, severity, reason, to_recipient,
                   body_path, resolved_at, source_event_id
              FROM escalations
             WHERE repository_id = ? AND resolved_at IS NULL
             ORDER BY ts DESC, id DESC
            """,
            (db.repository_id,),
        ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def get_escalation_history(
    db: RepoDb,
    limit: int = 100,
) -> tuple[EscalationRecord, ...]:
    """Return recent escalations (all rows), newest ``ts`` first."""
    rows = db.conn.execute(
            """
            SELECT id, ts, ticket_id, severity, reason, to_recipient,
                   body_path, resolved_at, source_event_id
              FROM escalations WHERE repository_id = ?
             ORDER BY ts DESC, id DESC
             LIMIT ?
            """,
            (db.repository_id, max(0, int(limit))),
        ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def ack_escalation_db(escalation_id: int, db: RepoDb) -> None:
    """Mark an escalation resolved by setting ``resolved_at`` to now."""
    from murder.state.persistence.escalations import resolve_escalation

    resolve_escalation(db, int(escalation_id))


def _record_from_row(row: object) -> EscalationRecord:
    return EscalationRecord(
        id=int(row["id"]),
        created_at=_parse_datetime(row["ts"]) or datetime.utcnow(),
        ticket_id=_optional_str(row["ticket_id"]),
        severity=int(row["severity"]),
        reason=str(row["reason"]),
        to_recipient=str(row["to_recipient"]),
        body_path=_optional_str(row["body_path"]),
        resolved_at=_parse_datetime(row["resolved_at"]),
        source_event_id=(
            None if row["source_event_id"] is None else int(row["source_event_id"])
        ),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
