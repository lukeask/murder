from __future__ import annotations

import asyncio
from typing import Any

from murder.state.persistence import escalations as dbmod
from murder.bus.protocol import CommandEvent
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec

MIN_ESCALATION_SEVERITY = 1
MAX_ESCALATION_SEVERITY = 3
DEFAULT_ESCALATION_SEVERITY = 2


class StateCommandWorker(Worker):
    """DB-backed state mutations requested by frontend clients."""

    ESCALATION_CREATE = OrchestrationCommand.STATE_ESCALATION_CREATE
    ESCALATION_ACK = OrchestrationCommand.STATE_ESCALATION_ACK

    def __init__(self) -> None:
        super().__init__(
            WorkerSpec(
                name=WorkerName.STATE,
                accepts=(self.ESCALATION_CREATE, self.ESCALATION_ACK),
                process_model="thread",
            )
        )

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if command.kind is self.ESCALATION_ACK:
            if ctx.db is None:
                raise RuntimeError("StateCommandWorker requires ctx.db")
            escalation_id = command.payload.get("escalation_id")
            if escalation_id is None:
                raise ValueError("state.escalation.ack requires escalation_id")
            dbmod.resolve_escalation(ctx.db, int(escalation_id))
            return {"handled": True, "escalation_id": int(escalation_id)}
        if command.kind is not self.ESCALATION_CREATE:
            return {"handled": False}
        if ctx.db is None:
            raise RuntimeError("StateCommandWorker requires ctx.db")
        reason = command.payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("state.escalation.create requires non-empty payload.reason")
        severity = int(command.payload.get("severity", DEFAULT_ESCALATION_SEVERITY))
        if severity < MIN_ESCALATION_SEVERITY or severity > MAX_ESCALATION_SEVERITY:
            raise ValueError("state.escalation.create payload.severity must be 1, 2, or 3")
        to_recipient = str(command.payload.get("to_recipient", "user"))
        if to_recipient not in {"user", "collaborator"}:
            raise ValueError(
                "state.escalation.create payload.to_recipient must be user or collaborator"
            )
        source_event_id = command.payload.get("source_event_id")
        escalation_id = dbmod.insert_escalation(
            ctx.db,
            ticket_id=command.payload.get("ticket_id"),
            severity=severity,
            reason=reason,
            to_recipient=to_recipient,
            source_event_id=int(source_event_id) if source_event_id is not None else None,
            body_path=command.payload.get("body_path"),
        )
        return {"handled": True, "escalation_id": escalation_id}
