"""SQLite read/write projections for the escalations table."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from murder_newstructure.persistence.schema import get_db


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


def get_active_escalations(db_path: Path) -> tuple[EscalationRecord, ...]:
    """Return unresolved escalations (``resolved_at IS NULL``), newest first."""
    with closing(get_db(Path(db_path))) as conn:
        rows = conn.execute(
            """
            SELECT id, ts, ticket_id, severity, reason, to_recipient,
                   body_path, resolved_at, source_event_id
              FROM escalations
             WHERE resolved_at IS NULL
             ORDER BY ts DESC, id DESC
            """
        ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def get_escalation_history(
    db_path: Path,
    limit: int = 100,
) -> tuple[EscalationRecord, ...]:
    """Return recent escalations (all rows), newest ``ts`` first."""
    with closing(get_db(Path(db_path))) as conn:
        rows = conn.execute(
            """
            SELECT id, ts, ticket_id, severity, reason, to_recipient,
                   body_path, resolved_at, source_event_id
              FROM escalations
             ORDER BY ts DESC, id DESC
             LIMIT ?
            """,
            (max(0, int(limit)),),
        ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def ack_escalation_db(escalation_id: int, db_path: Path) -> None:
    """Mark an escalation resolved by setting ``resolved_at`` to now."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with closing(get_db(Path(db_path))) as conn:
        conn.execute(
            """
            UPDATE escalations
               SET resolved = 1,
                   resolved_at = ?
             WHERE id = ?
            """,
            (now, int(escalation_id)),
        )


def _record_from_row(row: sqlite3.Row) -> EscalationRecord:
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
