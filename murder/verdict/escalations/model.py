"""Escalation queue helpers. Sync persistence. Bus publish uses EscalationService."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.state.persistence.connection import RepoDb
from murder.state.persistence.escalations import (
    insert_escalation,
    list_pending_escalations,
    resolve_escalation,
)
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.paths import escalation_md

if TYPE_CHECKING:
    from murder.runtime.orchestration.events import EscalationEvent


def queue_for_user(db: RepoDb, event: EscalationEvent) -> int:
    """Insert escalation row (no bus publish). Prefer EscalationService.escalate_to_user."""
    return insert_escalation(
        db,
        ticket_id=event.ticket_id,
        severity=int(event.severity),
        reason=event.reason,
        to_recipient="user",
    )


def queue_for_collaborator(
    db: RepoDb,
    event: EscalationEvent,
    body: str,
    repo_root: Path,
) -> tuple[int, Path]:
    """Insert row + write `.murder/agents/escalations/<id>.md`. Prefer EscalationService."""
    eid = insert_escalation(
        db,
        ticket_id=event.ticket_id,
        severity=int(event.severity),
        reason=event.reason,
        to_recipient="collaborator",
    )
    path = escalation_md(repo_root, eid)
    atomic_write_text(path, body)
    db.conn.execute(
        "UPDATE escalations SET body_path = ? WHERE repository_id = ? AND id = ?",
        (str(path), db.repository_id, eid),
    )
    return eid, path


def list_pending(db: RepoDb, recipient: str | None = None) -> list[dict[str, Any]]:
    return list_pending_escalations(db, recipient)


def resolve(db: RepoDb, escalation_id: int) -> None:
    resolve_escalation(db, escalation_id)
