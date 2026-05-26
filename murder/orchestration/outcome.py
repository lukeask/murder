"""Ticket failure and block boundary (W4)."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from murder.tickets import lifecycle
from murder.persistence.tickets import (
    get_ticket_status as _db_get_ticket_status,
    update_ticket_status as _db_update_ticket_status,
)
from murder.tickets.status import TicketStatus

if TYPE_CHECKING:
    from murder.escalations.service import EscalationService


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
        old = _db_get_ticket_status(self.conn, ticket_id)
        prev = TicketStatus(old) if old else TicketStatus.IN_PROGRESS
        try:
            prev = lifecycle.transition(self.conn, ticket_id, TicketStatus.FAILED)
        except Exception:
            _db_update_ticket_status(self.conn, ticket_id, TicketStatus.FAILED.value)
        lifecycle.set_last_error(self.conn, ticket_id, reason)
        await self.emit_status(ticket_id, prev, TicketStatus.FAILED.value)
        await self.escalations.record_ticket_failure(ticket_id, reason)

    async def block_ticket(self, ticket_id: str, reason: str, *, path: str | None = None) -> None:
        if path is not None:
            await self.escalations.block_writeset_violation(ticket_id, path)
            return
        _db_update_ticket_status(self.conn, ticket_id, TicketStatus.BLOCKED.value)
        await self.escalations.record_ticket_failure(ticket_id, reason)


__all__ = ["TicketOutcomeService"]
