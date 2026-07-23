"""Escalation queue helpers — sync persistence; bus publish via EscalationService."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.state.persistence.escalations import (
    insert_escalation,
    list_pending_escalations,
    resolve_escalation,
)
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.paths import escalation_md

if TYPE_CHECKING:
    from murder.runtime.orchestration.events import EscalationEvent


def queue_for_user(conn: sqlite3.Connection, event: EscalationEvent) -> int:
    """Insert escalation row (no bus publish). Prefer EscalationService.escalate_to_user."""
    return insert_escalation(
        conn,
        ticket_id=event.ticket_id,
        severity=int(event.severity),
        reason=event.reason,
        to_recipient="user",
    )


def queue_for_collaborator(
    conn: sqlite3.Connection,
    event: EscalationEvent,
    body: str,
    repo_root: Path,
) -> tuple[int, Path]:
    """Insert row + write `.murder/agents/escalations/<id>.md`. Prefer EscalationService."""
    eid = insert_escalation(
        conn,
        ticket_id=event.ticket_id,
        severity=int(event.severity),
        reason=event.reason,
        to_recipient="collaborator",
    )
    path = escalation_md(repo_root, eid)
    atomic_write_text(path, body)
    conn.execute(
        "UPDATE escalations SET body_path = ? WHERE id = ?",
        (str(path), eid),
    )
    return eid, path


def list_pending(conn: sqlite3.Connection, recipient: str | None = None) -> list[dict[str, Any]]:
    return list_pending_escalations(conn, recipient)


def resolve(conn: sqlite3.Connection, escalation_id: int) -> None:
    resolve_escalation(conn, escalation_id)
