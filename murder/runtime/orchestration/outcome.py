"""Ticket failure and block boundary (W4)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murder.state.persistence import tickets as ticket_db
from murder.work.tickets import lifecycle
from murder.work.tickets.status import TicketStatus

if TYPE_CHECKING:
    from murder.verdict.escalations.service import EscalationService

LOGGER = logging.getLogger(__name__)


EmitStatus = Callable[[str, TicketStatus | str, str], Awaitable[None]]
# (ticket_id) -> emit a key-only ``state.snapshot{entity=ticket}``. Used by the
# block path, which (unlike fail) has no typed StatusChangeEvent and so does not
# flow through ``emit_status``. Wired to ``Runtime.publish_snapshot``.
EmitSnapshot = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class TicketOutcomeService:
    conn: sqlite3.Connection
    repo_root: Path
    escalations: EscalationService
    emit_status: EmitStatus
    emit_snapshot: EmitSnapshot | None = None

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)

    async def fail_ticket(self, ticket_id: str, reason: str) -> None:
        old = ticket_db.get_ticket_status(self.conn, ticket_id)
        prev = TicketStatus(old) if old else TicketStatus.IN_PROGRESS
        try:
            prev = lifecycle.transition(self.conn, ticket_id, TicketStatus.FAILED)
        except Exception:
            ticket_db.update_ticket_status(self.conn, ticket_id, TicketStatus.FAILED.value)
        lifecycle.set_last_error(self.conn, ticket_id, reason)
        await self.emit_status(ticket_id, prev, TicketStatus.FAILED.value)
        await self.escalations.record_ticket_failure(ticket_id, reason)
        await self._prune_terminal_worktree(ticket_id)

    async def complete_ticket(self, ticket_id: str) -> None:
        """Normalize-then-complete: walk a ticket up to DONE, emit, prune.

        DONE is only reachable from IN_PROGRESS, but a reattach can observe a
        ``>>> DONE`` against a ticket still in READY (or, transiently, PLANNED/
        BLOCKED). Walk it up to IN_PROGRESS first rather than attempting an
        invalid raw READY/PLANNED -> DONE jump. An already-done ticket is a
        no-op: completion is idempotent, so a reattach that re-reads ``>>> DONE``
        from scrollback must not re-fire the transition (and the event/snapshot)
        against an already-terminal ticket. Terminal-but-not-done states
        (archived/failed) are not promotable; skip completion for them instead
        of raising InvalidTransition.
        """
        status = ticket_db.get_ticket_status(self.conn, ticket_id)
        if status == TicketStatus.DONE.value:
            return
        if status not in (
            TicketStatus.READY.value,
            TicketStatus.IN_PROGRESS.value,
            TicketStatus.BLOCKED.value,
            TicketStatus.PLANNED.value,
        ):
            LOGGER.warning(
                "completion: ticket %s is %s — not promotable to done, skipping",
                ticket_id,
                status,
            )
            return
        if status == TicketStatus.PLANNED.value:
            lifecycle.transition(self.conn, ticket_id, TicketStatus.READY, reason="completion")
            status = TicketStatus.READY.value
        if status in (TicketStatus.READY.value, TicketStatus.BLOCKED.value):
            lifecycle.transition(
                self.conn, ticket_id, TicketStatus.IN_PROGRESS, reason="completion"
            )
        prev = lifecycle.transition(self.conn, ticket_id, TicketStatus.DONE)
        # The ready->in_progress pre-transitions above are part of the SAME
        # logical "done"; emit_status fires the typed StatusChangeEvent plus the
        # key-only ticket snapshot once for the terminal transition (not per
        # intermediate lifecycle step).
        await self.emit_status(ticket_id, prev, TicketStatus.DONE.value)
        await self._prune_terminal_worktree(ticket_id)

    async def block_ticket(self, ticket_id: str, reason: str) -> None:
        ticket_db.update_ticket_status(self.conn, ticket_id, TicketStatus.BLOCKED.value)
        if self.emit_snapshot is not None:
            await self.emit_snapshot(ticket_id)
        await self.escalations.record_ticket_failure(ticket_id, reason)

    async def _prune_terminal_worktree(self, ticket_id: str) -> None:
        from murder.state.storage.worktrees import prune_terminal_crow_worktree

        try:
            await prune_terminal_crow_worktree(self.conn, self.repo_root, ticket_id)
        except Exception as exc:
            LOGGER.debug("worktree prune skipped for %s: %s", ticket_id, exc)


__all__ = ["TicketOutcomeService"]
