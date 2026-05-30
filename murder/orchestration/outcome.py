"""Ticket failure and block boundary (W4)."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murder.persistence import tickets as ticket_db
from murder.tickets import lifecycle
from murder.tickets.status import TicketStatus

if TYPE_CHECKING:
    from murder.escalations.service import EscalationService

LOGGER = logging.getLogger(__name__)


EmitStatus = Callable[[str, TicketStatus | str, str], Awaitable[None]]


@dataclass(slots=True)
class TicketOutcomeService:
    conn: sqlite3.Connection
    repo_root: Path
    escalations: EscalationService
    emit_status: EmitStatus

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

    async def block_ticket(self, ticket_id: str, reason: str, *, path: str | None = None) -> None:
        if path is not None:
            await self.escalations.block_writeset_violation(ticket_id, path)
            return
        ticket_db.update_ticket_status(self.conn, ticket_id, TicketStatus.BLOCKED.value)
        await self.escalations.record_ticket_failure(ticket_id, reason)

    async def _prune_terminal_worktree(self, ticket_id: str) -> None:
        from murder.storage.worktrees import prune_terminal_crow_worktree

        try:
            await prune_terminal_crow_worktree(self.conn, self.repo_root, ticket_id)
        except Exception as exc:
            LOGGER.debug("worktree prune skipped for %s: %s", ticket_id, exc)


__all__ = ["TicketOutcomeService"]
