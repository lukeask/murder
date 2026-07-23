from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from murder.app.service.command_dispatch import ClaimedCommand, CommandDispatcher
from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.workers.base import Worker, WorkerCommand, WorkerCtx
from murder.runtime.workers.process_runner import SubprocessWorkerRunner
from murder.runtime.workers.process_targets import usage_probe_process_target
from murder.state.persistence.commands import upsert_worker_heartbeat

LOGGER = logging.getLogger(__name__)


async def _await_cancelled_task(task: asyncio.Task[None], *, label: str) -> None:
    """Await a just-cancelled task, swallowing the cancellation but surfacing any
    *other* failure that surfaced during teardown at DEBUG. CancelledError is the
    expected outcome and is not logged."""
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.debug("task %r raised during cancellation/teardown", label, exc_info=True)


@dataclass
class _WorkerState:
    worker: Worker
    stop_event: asyncio.Event
    run_task: asyncio.Task[None] | None
    command_task: asyncio.Task[None]
    command_claim_task: asyncio.Task[None]
    heartbeat_task: asyncio.Task[None]
    commands: asyncio.Queue[WorkerCommand | CommandEvent | ClaimedCommand]
    runner: SubprocessWorkerRunner | None = None


class Supervisor:
    """Worker task lifecycle only; command durability lives in CommandDispatcher."""

    def __init__(
        self,
        ctx: WorkerCtx,
        *,
        command_poll_s: float = 0.25,
        command_dispatcher: CommandDispatcher | None = None,
    ) -> None:
        self._ctx = ctx
        if self._ctx.shutdown is None:
            self._ctx.shutdown = asyncio.Event()
        self._states: dict[WorkerName, _WorkerState] = {}
        self._command_poll_s = command_poll_s
        self._reaper_task: asyncio.Task[None] | None = None
        self._commands = command_dispatcher

    def _command_dispatcher(self) -> CommandDispatcher | None:
        return self._commands

    async def start_worker(self, worker: Worker) -> None:
        name = worker.spec.name
        if name in self._states:
            return
        stop_event = asyncio.Event()
        commands: asyncio.Queue[WorkerCommand | CommandEvent | ClaimedCommand] = asyncio.Queue()
        await worker.on_start(self._ctx)
        runner = await self._start_subprocess_runner(worker)
        run_task = (
            None
            if runner is not None
            else asyncio.create_task(worker.run(self._ctx, stop_event), name=f"{name}:run")
        )
        state = _WorkerState(
            worker=worker,
            stop_event=stop_event,
            run_task=run_task,
            command_task=asyncio.create_task(
                self._command_loop(worker, stop_event, commands, runner), name=f"{name}:commands"
            ),
            command_claim_task=asyncio.create_task(
                self._command_claim_loop(worker, stop_event, commands),
                name=f"{name}:command-claim",
            ),
            heartbeat_task=asyncio.create_task(
                self._heartbeat_loop(worker, stop_event, runner), name=f"{name}:heartbeat"
            ),
            commands=commands,
            runner=runner,
        )
        self._states[name] = state
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(
                self._command_reaper_loop(), name="supervisor:command-reaper"
            )

    async def stop_worker(self, name: WorkerName) -> None:
        state = self._states.pop(name, None)
        if state is None:
            return
        state.stop_event.set()
        for task in (state.command_task, state.command_claim_task, state.heartbeat_task):
            task.cancel()
            await _await_cancelled_task(task, label=f"{name}:{task.get_name()}")
        if state.runner is not None:
            await state.runner.stop(state.worker.spec.shutdown_grace_s)
        elif state.run_task is not None:
            try:
                await asyncio.wait_for(state.run_task, timeout=state.worker.spec.shutdown_grace_s)
            except asyncio.TimeoutError:
                state.run_task.cancel()
                await _await_cancelled_task(state.run_task, label=f"{name}:run")
        await state.worker.on_stop(self._ctx)

    async def stop_all(self) -> None:
        if self._ctx.shutdown is not None:
            self._ctx.shutdown.set()
        for name in list(self._states.keys()):
            await self.stop_worker(name)
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            await _await_cancelled_task(self._reaper_task, label="supervisor:command-reaper")
            self._reaper_task = None

    async def dispatch(self, worker_name: WorkerName, command: WorkerCommand) -> bool:
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
        commands: asyncio.Queue[WorkerCommand | CommandEvent | ClaimedCommand],
        runner: SubprocessWorkerRunner | None = None,
    ) -> None:
        while not stop_event.is_set():
            command = await commands.get()
            if runner is not None:
                await runner.dispatch(command)
                continue
            if isinstance(command, ClaimedCommand):
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

    async def _start_subprocess_runner(self, worker: Worker) -> SubprocessWorkerRunner | None:
        if worker.spec.process_model != "subprocess":
            return None
        if worker.spec.name != WorkerName.USAGE_PROBE:
            return None
        runner = SubprocessWorkerRunner(
            usage_probe_process_target,
            name=worker.spec.name,
            args=(str(self._ctx.repo_root), str(self._ctx.run_id or "")),
        )
        await runner.start()
        return runner

    async def _handle_command_event(
        self,
        worker: Worker,
        command: CommandEvent,
        *,
        command_id: str,
    ) -> None:
        dispatcher = self._command_dispatcher()
        if dispatcher is None:
            return
        # Renew immediately, then periodically while the side-effecting handler
        # is alive. A reaper may still recover the command if this worker dies,
        # but ordinary lease expiry can no longer start a duplicate operation.
        dispatcher.renew(command_id, claimed_by=worker.spec.name)
        renewal_task = asyncio.create_task(
            self._renew_command_lease(
                dispatcher,
                command_id=command_id,
                claimed_by=worker.spec.name,
            ),
            name=f"{worker.spec.name}:renew-command:{command_id}",
        )
        try:
            result = await worker.on_command(command, self._ctx)
        except Exception as exc:
            # An exception may declare itself non-retryable (e.g. WorktreeError):
            # a deterministic failure that would fail identically on retry. Such
            # an error overrides the command's own retry policy so we fail fast
            # to escalation instead of burning the ~90s retry budget.
            retryable = command.retryable and getattr(exc, "retryable", True)
            dispatcher.fail(
                command_id=command_id,
                last_error=str(exc),
                retryable=retryable,
            )
            return
        finally:
            renewal_task.cancel()
            await _await_cancelled_task(renewal_task, label=renewal_task.get_name())
        dispatcher.finish(
            command_id=command_id,
            command=command,
            worker_name=worker.spec.name,
            result=result,
        )

    @staticmethod
    async def _renew_command_lease(
        dispatcher: CommandDispatcher,
        *,
        command_id: str,
        claimed_by: str,
    ) -> None:
        interval = max(0.05, dispatcher.lease_ttl_s / 3)
        while True:
            await asyncio.sleep(interval)
            if not dispatcher.renew(command_id, claimed_by=claimed_by):
                return

    async def _command_claim_loop(
        self,
        worker: Worker,
        stop_event: asyncio.Event,
        commands: asyncio.Queue[WorkerCommand | CommandEvent | ClaimedCommand],
    ) -> None:
        dispatcher = self._command_dispatcher()
        if dispatcher is None:
            return

        while not stop_event.is_set():
            claimed = dispatcher.claim_next(
                target_worker=worker.spec.name,
                claimed_by=worker.spec.name,
            )
            if claimed is None:
                await asyncio.sleep(self._command_poll_s)
                continue
            await commands.put(claimed)

    async def _command_reaper_loop(self) -> None:
        dispatcher = self._command_dispatcher()
        if dispatcher is None:
            return

        while self._ctx.shutdown is None or not self._ctx.shutdown.is_set():
            await asyncio.sleep(dispatcher.reaper_interval_s)
            reaped = dispatcher.reap_stale()
            await dispatcher.escalate_retry_exhaustion(reaped["failed"])

    async def _heartbeat_loop(
        self,
        worker: Worker,
        stop_event: asyncio.Event,
        runner: SubprocessWorkerRunner | None = None,
    ) -> None:
        interval = max(0.05, worker.spec.heartbeat_s)
        while not stop_event.is_set():
            if self._ctx.db is not None and self._ctx.run_id is not None:
                upsert_worker_heartbeat(
                    self._ctx.db,
                    worker_id=worker.spec.name,
                    run_id=self._ctx.run_id,
                    role=worker.spec.name,
                    payload={
                        "process_model": worker.spec.process_model,
                        "pid": runner.pid if runner is not None else None,
                    },
                )
            callback = self._ctx.on_heartbeat
            if callback is not None:
                await callback(worker.spec.name)
            await asyncio.sleep(interval)
