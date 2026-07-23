"""SQLite adapter and coordinating submitter for durable worker commands."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.ports import CommandRepository, OrchestrationEventSink


@dataclass(frozen=True, slots=True)
class SqliteCommandRepository:
    connection: sqlite3.Connection

    def add(self, command: CommandEvent) -> None:
        # Keep persistence behind the adapter method. Importing the persistence
        # package at module load would couple port discovery to the ticket
        # package's initialization order.
        from murder.state.persistence.commands import enqueue_command  # noqa: PLC0415

        enqueue_command(
            self.connection,
            command_id=str(command.id),
            run_id=command.run_id,
            agent_id=command.agent_id,
            role=command.role.value if command.role is not None else None,
            ticket_id=command.ticket_id,
            target_worker=command.target_worker,
            kind=command.kind,
            payload=command.payload,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            status=command.status.value,
            claimed_by=command.claimed_by,
            lease_expires_at=command.lease_expires_at,
            attempt_count=command.attempt_count,
            retryable=command.retryable,
            result=command.result,
        )


@dataclass(frozen=True, slots=True)
class PersistingCommandSubmitter:
    """Persist first, then emit an optional in-process wakeup/observation."""

    repository: CommandRepository
    events: OrchestrationEventSink | None = None

    async def submit(self, command: CommandEvent) -> None:
        self.repository.add(command)
        if self.events is not None:
            await self.events.publish(command)


__all__ = ["PersistingCommandSubmitter", "SqliteCommandRepository"]
