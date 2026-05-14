from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder import db as dbmod
from murder.bus import EscalationEvent
from murder.supervisor import Supervisor
from murder.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec

EXPECTED_ATTEMPTS_AFTER_RECLAIM = 2


class _CommandWorker(Worker):
    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        super().__init__(
            WorkerSpec(name="collaborator", heartbeat_s=10.0, shutdown_grace_s=0.1)
        )
        self.handled: list[str] = []
        self.fail_on = fail_on or set()

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:
        del ctx
        if command.name in self.fail_on:
            raise RuntimeError(f"boom:{command.name}")
        if command.name.startswith("unknown."):
            return False
        self.handled.append(command.name)
        return True


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _seed_run(conn: sqlite3.Connection) -> None:
    dbmod.insert_run(conn, "r1", "{}")


def _enqueue(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    kind: str,
    status: str = "pending",
    attempt_count: int = 0,
    retryable: bool = True,
    claimed_by: str | None = None,
    lease_expires_at: int | None = None,
) -> None:
    dbmod.enqueue_command(
        conn,
        command_id=command_id,
        run_id="r1",
        agent_id="agent-1",
        role="collaborator",
        ticket_id="T-1",
        target_worker="collaborator",
        kind=kind,
        payload={"command_id": command_id},
        correlation_id=f"corr-{command_id}",
        idempotency_key=f"idem-{command_id}",
        status=status,
        attempt_count=attempt_count,
        retryable=retryable,
        claimed_by=claimed_by,
        lease_expires_at=lease_expires_at,
    )


async def _wait_for_row(
    conn: sqlite3.Connection,
    command_id: str,
    status: str,
) -> sqlite3.Row:
    for _ in range(100):
        row = conn.execute(
            "SELECT * FROM commands WHERE id = ?",
            (command_id,),
        ).fetchone()
        if row is not None and row["status"] == status:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError(f"command {command_id} did not reach {status}")


def _supervisor(ctx: WorkerCtx) -> Supervisor:
    return Supervisor(
        ctx,
        command_poll_s=0.01,
        command_lease_ttl_s=0.05,
        command_reaper_interval_s=0.01,
        max_command_attempts=3,
    )


@pytest.mark.asyncio
async def test_supervisor_claims_pending_command_and_completes_when_worker_handles(
    memdb: sqlite3.Connection,
) -> None:
    _seed_run(memdb)
    _enqueue(memdb, command_id="cmd-1", kind="collaborator.chat_send")
    worker = _CommandWorker()
    sup = _supervisor(WorkerCtx(repo_root=Path("."), db=memdb, run_id="r1"))

    await sup.start_worker(worker)
    try:
        row = await _wait_for_row(memdb, "cmd-1", "done")
    finally:
        await sup.stop_all()

    assert worker.handled == ["collaborator.chat_send"]
    assert row["claimed_by"] == "collaborator"
    assert row["attempt_count"] == 1
    assert '"handled": true' in row["result_json"]


@pytest.mark.asyncio
async def test_supervisor_fails_command_when_worker_reports_unhandled(
    memdb: sqlite3.Connection,
) -> None:
    _seed_run(memdb)
    _enqueue(memdb, command_id="cmd-1", kind="unknown.not_supported")
    worker = _CommandWorker()
    sup = _supervisor(WorkerCtx(repo_root=Path("."), db=memdb, run_id="r1"))

    await sup.start_worker(worker)
    try:
        row = await _wait_for_row(memdb, "cmd-1", "failed")
    finally:
        await sup.stop_all()

    assert row["retryable"] == 0
    assert "did not handle" in row["last_error"]
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_supervisor_fails_command_when_worker_raises(memdb: sqlite3.Connection) -> None:
    _seed_run(memdb)
    _enqueue(memdb, command_id="cmd-1", kind="collaborator.refresh")
    worker = _CommandWorker(fail_on={"collaborator.refresh"})
    sup = _supervisor(WorkerCtx(repo_root=Path("."), db=memdb, run_id="r1"))

    await sup.start_worker(worker)
    try:
        row = await _wait_for_row(memdb, "cmd-1", "failed")
    finally:
        await sup.stop_all()

    assert row["retryable"] == 1
    assert row["last_error"] == "boom:collaborator.refresh"
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_supervisor_reaper_requeues_stale_command_for_later_poll(
    memdb: sqlite3.Connection,
) -> None:
    _seed_run(memdb)
    _enqueue(
        memdb,
        command_id="cmd-stale",
        kind="collaborator.chat_send",
        status="in_flight",
        claimed_by="old-worker",
        lease_expires_at=0,
    )
    worker = _CommandWorker()
    sup = _supervisor(WorkerCtx(repo_root=Path("."), db=memdb, run_id="r1"))

    await sup.start_worker(worker)
    try:
        row = await _wait_for_row(memdb, "cmd-stale", "done")
    finally:
        await sup.stop_all()

    assert worker.handled == ["collaborator.chat_send"]
    assert row["claimed_by"] == "collaborator"
    assert row["attempt_count"] == EXPECTED_ATTEMPTS_AFTER_RECLAIM


@pytest.mark.asyncio
async def test_supervisor_reaper_publishes_escalation_for_terminal_failures(
    memdb: sqlite3.Connection,
) -> None:
    _seed_run(memdb)
    _enqueue(
        memdb,
        command_id="cmd-exhausted",
        kind="collaborator.chat_send",
        status="in_flight",
        attempt_count=2,
        retryable=True,
        claimed_by="old-worker",
        lease_expires_at=0,
    )
    bus = _RecordingBus()
    sup = _supervisor(WorkerCtx(repo_root=Path("."), db=memdb, bus=bus, run_id="r1"))

    await sup.start_worker(_CommandWorker())
    try:
        row = await _wait_for_row(memdb, "cmd-exhausted", "failed")
        for _ in range(100):
            if bus.events:
                break
            await asyncio.sleep(0.01)
    finally:
        await sup.stop_all()

    assert row["last_error"] == "command lease expired"
    assert len(bus.events) == 1
    assert isinstance(bus.events[0], EscalationEvent)
    assert bus.events[0].ticket_id == "T-1"
    assert "cmd-exhausted" in bus.events[0].reason
