"""Ticket status transition rules (D7)."""

from __future__ import annotations

from datetime import datetime

from murder.state.persistence import tickets as db
from murder.state.persistence.connection import RepoDb
from murder.work.tickets.status import TicketStatus


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


VALID_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.DRAFT: {TicketStatus.PLANNED, TicketStatus.ARCHIVED},
    TicketStatus.PLANNED: {TicketStatus.READY, TicketStatus.ARCHIVED},
    TicketStatus.READY: {TicketStatus.IN_PROGRESS, TicketStatus.PLANNED, TicketStatus.ARCHIVED},
    TicketStatus.IN_PROGRESS: {
        TicketStatus.DONE,
        TicketStatus.BLOCKED,
        TicketStatus.FAILED,
        TicketStatus.ARCHIVED,
    },
    TicketStatus.BLOCKED: {TicketStatus.IN_PROGRESS, TicketStatus.FAILED, TicketStatus.ARCHIVED},
    TicketStatus.FAILED: {TicketStatus.PLANNED, TicketStatus.READY, TicketStatus.ARCHIVED},
    TicketStatus.DONE: {TicketStatus.PLANNED, TicketStatus.ARCHIVED},  # D7: reopen
    TicketStatus.ARCHIVED: {TicketStatus.PLANNED},  # un-archive
}


class InvalidTransition(Exception):
    pass


def transition(
    repo_db: RepoDb,
    ticket_id: str,
    to: TicketStatus,
    *,
    reason: str | None = None,
) -> TicketStatus:
    """Apply a status change. Returns the previous status. Caller emits the event."""
    prev_str = db.get_ticket_status(repo_db, ticket_id)
    if prev_str is None:
        raise KeyError(f"ticket not found: {ticket_id}")
    prev = TicketStatus(prev_str)
    if prev == to:
        return prev
    if to not in VALID_TRANSITIONS.get(prev, set()):
        raise InvalidTransition(
            f"{ticket_id}: {prev.value} → {to.value} not allowed"
            + (f" (reason={reason})" if reason else "")
        )
    db.update_ticket_status(repo_db, ticket_id, to.value)
    return prev


def clear_last_error(repo_db: RepoDb, ticket_id: str) -> None:
    """Clear last_error on a ticket (called after successful retry transition)."""
    repo_db.conn.execute(
        "UPDATE tickets SET last_error = NULL, updated_at = ? WHERE repository_id = ? AND id = ?",
        (_now(), repo_db.repository_id, ticket_id),
    )


def set_last_error(repo_db: RepoDb, ticket_id: str, error: str) -> None:
    """Record the terminal failure reason on a ticket."""
    repo_db.conn.execute(
        "UPDATE tickets SET last_error = ?, updated_at = ? WHERE repository_id = ? AND id = ?",
        (error, _now(), repo_db.repository_id, ticket_id),
    )


def reopen(repo_db: RepoDb, ticket_id: str) -> list[str]:
    """D7 reopen path: done → planned + cascade dependents back to planned.

    Returns ids of dependents cascaded. Caller stops running crows for them.
    """
    transition(repo_db, ticket_id, TicketStatus.PLANNED, reason="reopened")
    cascaded: list[str] = []
    for dep_id in db.dependents_of(repo_db, ticket_id):
        cur = db.get_ticket_status(repo_db, dep_id)
        if cur in {
            TicketStatus.READY.value,
            TicketStatus.IN_PROGRESS.value,
            TicketStatus.BLOCKED.value,
        }:
            try:
                transition(repo_db, dep_id, TicketStatus.PLANNED, reason="upstream_reopened")
                cascaded.append(dep_id)
            except InvalidTransition:
                # Some statuses (e.g. failed) are not reachable from PLANNED via this
                # path. Skip them. The user resolves manually.
                continue
    return cascaded
