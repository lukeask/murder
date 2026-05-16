from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.bus.protocol import CommandEvent
from murder.workers.base import Worker, WorkerCtx, WorkerSpec

KickoffReady = Callable[[str | None], Awaitable[list[str]]]
ApplyCarveReady = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
CaptureSubmit = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RetryFailed = Callable[[str], Awaitable[dict[str, Any]]]
SetScheduleAt = Callable[[str, str | None], Awaitable[dict[str, Any]]]


class OrchestratorCommandWorker(Worker):
    def __init__(
        self,
        *,
        kickoff_ready: KickoffReady,
        apply_carve_ready: ApplyCarveReady,
        capture_submit: CaptureSubmit,
        retry_failed: RetryFailed,
        set_schedule_at: SetScheduleAt,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name="orchestrator",
                process_model="thread",
                accepts=(
                    "scheduler.kickoff_ready",
                    "notetaker.capture.submit",
                    "ticket.apply_carve_ready",
                    "ticket.retry_failed",
                    "ticket.set_schedule_at",
                ),
            )
        )
        self._kickoff_ready = kickoff_ready
        self._apply_carve_ready = apply_carve_ready
        self._capture_submit = capture_submit
        self._retry_failed = retry_failed
        self._set_schedule_at = set_schedule_at

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:  # noqa: ARG002
        if command.kind == "scheduler.kickoff_ready":
            only = command.payload.get("only")
            if only is not None and not isinstance(only, str):
                raise ValueError("scheduler.kickoff_ready payload.only must be a string or null")
            kicked = await self._kickoff_ready(only)
            return {"handled": True, "kicked": kicked}
        if command.kind == "ticket.apply_carve_ready":
            ticket_id = command.payload.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.apply_carve_ready requires ticket_id")
            carve = command.payload.get("carve")
            yaml_text = command.payload.get("yaml")
            if not (
                (isinstance(carve, dict) and carve)
                or (isinstance(yaml_text, str) and yaml_text.strip())
            ):
                raise ValueError(
                    "ticket.apply_carve_ready requires non-empty 'carve' or 'yaml' in payload"
                )
            return await self._apply_carve_ready(ticket_id.strip(), command.payload)
        if command.kind == "notetaker.capture.submit":
            result = await self._capture_submit(dict(command.payload))
            return {"handled": True, **result}
        if command.kind == "ticket.retry_failed":
            ticket_id = command.payload.get("ticket_id")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.retry_failed requires ticket_id")
            return await self._retry_failed(ticket_id.strip())
        if command.kind == "ticket.set_schedule_at":
            ticket_id = command.payload.get("ticket_id")
            schedule_at = command.payload.get("schedule_at")
            if not isinstance(ticket_id, str) or not ticket_id.strip():
                raise ValueError("ticket.set_schedule_at requires ticket_id")
            if schedule_at is not None and not isinstance(schedule_at, str):
                raise ValueError("ticket.set_schedule_at requires string or null schedule_at")
            return await self._set_schedule_at(ticket_id.strip(), schedule_at)
        return {"handled": False}
