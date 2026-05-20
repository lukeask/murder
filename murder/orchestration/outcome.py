"""Ticket completion, failure, and validation boundary (W4)."""

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
    get_ticket as _db_get_ticket,
)
from murder.tickets.status import TicketStatus

from murder.orchestration.validator import (
    CompletionContext,
    ValidatorOutcome,
    ValidatorPipeline,
    first_failure_message,
    policy,
)

if TYPE_CHECKING:
    from murder.escalations.service import EscalationService


EmitStatus = Callable[[str, TicketStatus | str, str], Awaitable[None]]


@dataclass(slots=True)
class TicketOutcomeService:
    conn: sqlite3.Connection
    repo_root: Path
    escalations: EscalationService
    emit_status: EmitStatus
    pipeline: ValidatorPipeline | None = None

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        if self.pipeline is None:
            self.pipeline = ValidatorPipeline()

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

    async def complete_after_crow(
        self,
        ticket_id: str,
        *,
        start_commit: str | None,
    ) -> bool:
        row = _db_get_ticket(self.conn, ticket_id)
        if row is None:
            return False
        write_set = tuple(Path(p) for p in row.get("write_set") or [])
        context = CompletionContext(
            ticket_id=ticket_id,
            write_set=write_set,
            repo_root=self.repo_root,
            db=self.conn,
            start_commit=start_commit,
        )
        results = await self.pipeline.run(context)  # type: ignore[union-attr]
        outcome = policy(results)
        if outcome == ValidatorOutcome.PASS:
            prev = lifecycle.transition(self.conn, ticket_id, TicketStatus.DONE)
            await self.emit_status(ticket_id, prev, TicketStatus.DONE.value)
            return True
        message = first_failure_message(results)
        if outcome == ValidatorOutcome.BLOCKED:
            path = _path_from_blocked_message(message, write_set)
            await self.block_ticket(ticket_id, message, path=path)
            return False
        await self.fail_ticket(ticket_id, message)
        return False


def _path_from_blocked_message(message: str, write_set: tuple[Path, ...]) -> str:
    """Best-effort path for write-set escalation when diff validator blocks."""
    for path in write_set:
        if str(path) in message:
            return str(path)
    return str(write_set[0]) if write_set else "unknown"


__all__ = ["TicketOutcomeService"]
