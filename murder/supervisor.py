from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from murder import db as dbmod
from murder.bus.protocol import (
    COMMAND_REAPER_INTERVAL_S,
    DEFAULT_LEASE_TTL_S,
    DEFAULT_MAX_COMMAND_ATTEMPTS,
    CommandEvent,
    CommandStatus,
    EscalationEvent,
    Role,
)
from murder.workers.base import Worker, WorkerCommand, WorkerCtx


@dataclass(frozen=True)
class _ClaimedCommand:
    command_id: str
    event: CommandEvent


@dataclass
class _WorkerState:
    worker: Worker
    stop_event: asyncio.Event
    run_task: asyncio.Task[None]
    command_task: asyncio.Task[None]
    command_claim_task: asyncio.Task[None]
    heartbeat_task: asyncio.Task[None]
    commands: asyncio.Queue[WorkerCommand | CommandEvent | _ClaimedCommand]


class Supervisor:
    def __init__(
        self,
        ctx: WorkerCtx,
        *,
        command_poll_s: float = 0.25,
        command_lease_ttl_s: float = DEFAULT_LEASE_TTL_S,
        command_reaper_interval_s: float = COMMAND_REAPER_INTERVAL_S,
        max_command_attempts: int = DEFAULT_MAX_COMMAND_ATTEMPTS,
    ) -> None:
        self._ctx = ctx
        if self._ctx.shutdown is None:
            self._ctx.shutdown = asyncio.Event()
        self._states: dict[str, _WorkerState] = {}
        self._command_poll_s = command_poll_s
        self._command_lease_ttl_s = command_lease_ttl_s
        self._command_reaper_interval_s = command_reaper_interval_s
        self._max_command_attempts = max_command_attempts
        self._reaper_task: asyncio.Task[None] | None = None

    async def start_worker(self, worker: Worker) -> None:
        name = worker.spec.name
        if name in self._states:
            return
        stop_event = asyncio.Event()
        commands: asyncio.Queue[WorkerCommand | CommandEvent | _ClaimedCommand] = asyncio.Queue()
        await worker.on_start(self._ctx)
        state = _WorkerState(
            worker=worker,
            stop_event=stop_event,
            run_task=asyncio.create_task(worker.run(self._ctx, stop_event), name=f"{name}:run"),
            command_task=asyncio.create_task(
                self._command_loop(worker, stop_event, commands), name=f"{name}:commands"
            ),
            command_claim_task=asyncio.create_task(
                self._command_claim_loop(worker, stop_event, commands),
                name=f"{name}:command-claim",
            ),
            heartbeat_task=asyncio.create_task(
                self._heartbeat_loop(worker, stop_event), name=f"{name}:heartbeat"
            ),
            commands=commands,
        )
        self._states[name] = state
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(
                self._command_reaper_loop(), name="supervisor:command-reaper"
            )

    async def stop_worker(self, name: str) -> None:
        state = self._states.pop(name, None)
        if state is None:
            return
        state.stop_event.set()
        for task in (state.command_task, state.command_claim_task, state.heartbeat_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        try:
            await asyncio.wait_for(state.run_task, timeout=state.worker.spec.shutdown_grace_s)
        except asyncio.TimeoutError:
            state.run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await state.run_task
        await state.worker.on_stop(self._ctx)

    async def stop_all(self) -> None:
        if self._ctx.shutdown is not None:
            self._ctx.shutdown.set()
        for name in list(self._states.keys()):
            await self.stop_worker(name)
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper_task
            self._reaper_task = None

    async def dispatch(self, worker_name: str, command: WorkerCommand) -> bool:
        state = self._states.get(worker_name)
        if state is None:
            return False
        await state.commands.put(command)
        return True

    async def dispatch_event(self, command: CommandEvent) -> bool:
        state = self._states.get(command.target_worker)
        if state is None:
            return False
        await state.commands.put(command)
        return True

    async def _command_loop(
        self,
        worker: Worker,
        stop_event: asyncio.Event,
        commands: asyncio.Queue[WorkerCommand | CommandEvent | _ClaimedCommand],
    ) -> None:
        while not stop_event.is_set():
            command = await commands.get()
            if isinstance(command, _ClaimedCommand):
                await self._handle_command_event(
                    worker,
                    command.event,
                    command_id=command.command_id,
                )
                continue
            if isinstance(command, CommandEvent):
                await self._handle_command_event(worker, command, command_id=str(command.id))
                continue
            with contextlib.suppress(Exception):
                await worker.handle_command(command, self._ctx)

    async def _handle_command_event(
        self,
        worker: Worker,
        command: CommandEvent,
        *,
        command_id: str,
    ) -> None:
        try:
            result = await worker.on_command(command, self._ctx)
            if result.get("handled") is False:
                if self._ctx.db is not None:
                    dbmod.fail_command(
                        self._ctx.db,
                        command_id=command_id,
                        last_error=(
                            f"worker {worker.spec.name!r} did not handle {command.kind!r}"
                        ),
                        retryable=False,
                    )
                return
        except Exception as exc:
            if self._ctx.db is not None:
                dbmod.fail_command(
                    self._ctx.db,
                    command_id=command_id,
                    last_error=str(exc),
                    retryable=command.retryable,
                )
            return
        if self._ctx.db is not None:
            dbmod.complete_command(self._ctx.db, command_id=command_id, result=result)

    async def _command_claim_loop(
        self,
        worker: Worker,
        stop_event: asyncio.Event,
        commands: asyncio.Queue[WorkerCommand | CommandEvent | _ClaimedCommand],
    ) -> None:
        if self._ctx.db is None:
            return

        while not stop_event.is_set():
            lease_expires_at = math.ceil(time.time() + self._command_lease_ttl_s)
            row = dbmod.claim_next_command(
                self._ctx.db,
                target_worker=worker.spec.name,
                claimed_by=worker.spec.name,
                lease_expires_at=lease_expires_at,
            )
            if row is None:
                await asyncio.sleep(self._command_poll_s)
                continue
            await commands.put(
                _ClaimedCommand(
                    command_id=str(row["id"]),
                    event=self._command_from_row(row),
                )
            )

    async def _command_reaper_loop(self) -> None:
        if self._ctx.db is None:
            return

        while self._ctx.shutdown is None or not self._ctx.shutdown.is_set():
            await asyncio.sleep(self._command_reaper_interval_s)
            reaped = dbmod.reap_stale_commands(
                self._ctx.db,
                now_epoch=int(time.time()),
                max_attempts=self._max_command_attempts,
            )
            for command_id in reaped["failed"]:
                await self._publish_failed_command_escalation(command_id)

    async def _heartbeat_loop(self, worker: Worker, stop_event: asyncio.Event) -> None:
        interval = max(0.05, worker.spec.heartbeat_s)
        while not stop_event.is_set():
            if self._ctx.db is not None and self._ctx.run_id is not None:
                dbmod.upsert_worker_heartbeat(
                    self._ctx.db,
                    worker_id=worker.spec.name,
                    run_id=self._ctx.run_id,
                    role=worker.spec.name,
                    payload={"process_model": worker.spec.process_model},
                )
            callback = self._ctx.on_heartbeat
            if callback is not None:
                await callback(worker.spec.name)
            await asyncio.sleep(interval)

    def _command_from_row(self, row: dict[str, Any]) -> CommandEvent:
        role = row.get("role")
        status = row.get("status") or CommandStatus.PENDING.value
        try:
            event_id = UUID(str(row["id"]))
        except ValueError:
            event_id = uuid4()
        return CommandEvent(
            id=event_id,
            run_id=row["run_id"],
            agent_id=row.get("agent_id") or "",
            role=Role(role) if role else None,
            ticket_id=row.get("ticket_id"),
            target_worker=row["target_worker"],
            kind=row["kind"],
            payload=json.loads(row.get("payload_json") or "{}"),
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            status=CommandStatus(status),
            claimed_by=row.get("claimed_by"),
            lease_expires_at=row.get("lease_expires_at"),
            attempt_count=int(row.get("attempt_count") or 0),
            retryable=bool(row.get("retryable")),
            result=json.loads(row["result_json"]) if row.get("result_json") else None,
        )

    async def _publish_failed_command_escalation(self, command_id: str) -> None:
        if self._ctx.db is None or self._ctx.bus is None:
            return
        row = self._ctx.db.execute(
            "SELECT * FROM commands WHERE id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            return
        await self._ctx.bus.publish(
            EscalationEvent(
                run_id=row["run_id"],
                agent_id="supervisor",
                role=None,
                ticket_id=row["ticket_id"],
                to="user",
                severity=2,
                reason=(
                    f"Command {command_id} for worker {row['target_worker']} "
                    f"failed after retry exhaustion: {row['last_error'] or 'unknown error'}"
                ),
            )
        )
