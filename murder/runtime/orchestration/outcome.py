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
