from __future__ import annotations

import asyncio
import queue
import sqlite3
import threading
from pathlib import Path

import pytest

from murder import db as dbmod
from murder.bus import CommandEvent, Role
from murder.supervisor import Supervisor
from murder.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec
from murder.workers.thread_runner import ThreadWorkerRunner


class _DummyWorker(Worker):
    def __init__(self) -> None:
        super().__init__(WorkerSpec(name="dummy", heartbeat_s=0.01, shutdown_grace_s=0.2))
        self.started = False
        self.stopped = False
        self.commands: list[str] = []

    async def on_start(self, ctx: WorkerCtx) -> None:  # noqa: ARG002
        self.started = True

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def on_stop(self, ctx: WorkerCtx) -> None:  # noqa: ARG002
        self.stopped = True

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:  # noqa: ARG002
        self.commands.append(command.name)
        return True


@pytest.mark.asyncio
async def test_supervisor_heartbeat_command_and_shutdown() -> None:
    beats: list[str] = []

    async def _beat(name: str) -> None:
        beats.append(name)

    ctx = WorkerCtx(repo_root=Path("."), on_heartbeat=_beat)
    sup = Supervisor(ctx)
    worker = _DummyWorker()

    await sup.start_worker(worker)
    await sup.dispatch("dummy", WorkerCommand("refresh"))
    await sup.dispatch_event(
        CommandEvent(
            run_id="r1",
            agent_id="client-1",
            role=Role.COLLABORATOR,
            target_worker="dummy",
            kind="bus-refresh",
            payload={"scope": "all"},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        )
    )
    await asyncio.sleep(0.03)
    await sup.stop_worker("dummy")

    assert worker.started is True
    assert worker.stopped is True
    assert worker.commands == ["refresh", "bus-refresh"]
    assert beats
    assert all(name == "dummy" for name in beats)


@pytest.mark.asyncio
async def test_supervisor_writes_worker_heartbeat_rows(memdb: sqlite3.Connection) -> None:
    dbmod.insert_run(memdb, "r1", "{}")
    ctx = WorkerCtx(repo_root=Path("."), db=memdb, run_id="r1")
    sup = Supervisor(ctx)
    worker = _DummyWorker()

    await sup.start_worker(worker)
    await asyncio.sleep(0.03)
    await sup.stop_worker("dummy")

    row = memdb.execute(
        "SELECT run_id, role, payload_json FROM worker_heartbeats WHERE worker_id = 'dummy'"
    ).fetchone()
    assert row is not None
    assert row["run_id"] == "r1"
    assert row["role"] == "dummy"
    assert "process_model" in row["payload_json"]


@pytest.mark.asyncio
async def test_thread_worker_runner_stops() -> None:
    exited = threading.Event()

    def _target(stop: threading.Event, commands: queue.Queue[WorkerCommand]) -> None:  # noqa: ARG001
        while not stop.is_set():
            stop.wait(0.01)
        exited.set()

    runner = ThreadWorkerRunner(_target, name="thread-test")
    await runner.start()
    await runner.dispatch(WorkerCommand("noop"))
    await runner.stop(0.2)

    assert exited.is_set()
