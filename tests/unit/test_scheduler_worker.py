"""Unit tests for SchedulerWorker: mode persistence and autorun_ready tick."""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from murder.scheduler.worker import SchedulerWorker
from murder.workers.base import WorkerCtx


def _make_ctx(db: sqlite3.Connection) -> WorkerCtx:
    return WorkerCtx(
        repo_root=None,  # type: ignore[arg-type]
        db=db,
        bus=None,
        run_id="run-test-001",
        shutdown=None,
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    status: str = "ready",
    wave: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, 0, '2026-01-01', '2026-01-01')
        """,
        (ticket_id, f"title-{ticket_id}", wave, status),
    )


# --- on_start initialises scheduler_state row --------------------------------


def test_on_start_inserts_default_row(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    row = memdb.execute("SELECT mode FROM scheduler_state WHERE id = 1").fetchone()
    assert row is not None
    assert row["mode"] == "manual"


def test_on_start_is_idempotent(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    asyncio.run(worker.on_start(ctx))  # second call must not fail
    rows = memdb.execute("SELECT COUNT(*) AS n FROM scheduler_state").fetchone()
    assert rows["n"] == 1


# --- scheduler.set_mode command ----------------------------------------------


def _make_command_event(kind: str, payload: dict[str, Any]):  # type: ignore[type-arg]
    from murder.bus.protocol import CommandEvent

    return CommandEvent(
        run_id="run-test-001",
        agent_id="tui",
        target_worker="scheduler",
        kind=kind,
        payload=payload,
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )


def test_set_mode_manual_to_autorun(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = _make_command_event("scheduler.set_mode", {"mode": "autorun_ready"})
    result = asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))
    assert result["handled"] is True
    assert result["from_mode"] == "manual"
    assert result["to_mode"] == "autorun_ready"
    row = memdb.execute("SELECT mode FROM scheduler_state WHERE id = 1").fetchone()
    assert row["mode"] == "autorun_ready"


def test_set_mode_rejects_unknown_mode(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = _make_command_event("scheduler.set_mode", {"mode": "flying_spaghetti"})
    with pytest.raises(ValueError, match="unknown mode"):
        asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))


def test_set_mode_publishes_event(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    bus = AsyncMock()
    ctx = WorkerCtx(
        repo_root=None,  # type: ignore[arg-type]
        db=memdb,
        bus=bus,
        run_id="run-test-001",
    )
    cmd = _make_command_event("scheduler.set_mode", {"mode": "autorun_ready"})
    asyncio.run(worker.on_command(cmd, ctx))
    bus.publish.assert_awaited_once()
    published = bus.publish.call_args[0][0]
    assert published.type == "scheduler.mode"
    assert published.from_mode == "manual"
    assert published.to_mode == "autorun_ready"


def test_unknown_command_returns_not_handled(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = _make_command_event("something.else", {})
    result = asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))
    assert result["handled"] is False


# --- _tick logic -------------------------------------------------------------


def test_tick_does_nothing_in_manual_mode(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t001", status="ready")
    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    # mode stays manual
    worker._tick_seq = 1
    asyncio.run(worker._tick(_make_ctx(memdb)))
    rows = memdb.execute("SELECT COUNT(*) AS n FROM commands").fetchone()
    assert rows["n"] == 0


def test_tick_does_nothing_with_no_ready_tickets(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'autorun_ready' WHERE id = 1")
    _insert_ticket(memdb, "t002", status="planned")
    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))
    rows = memdb.execute("SELECT COUNT(*) AS n FROM commands").fetchone()
    assert rows["n"] == 0


def test_tick_submits_kickoff_when_autorun_and_ready(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'autorun_ready' WHERE id = 1")
    # Need a run row for the FK constraint
    memdb.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-test-001", "2026-01-01T00:00:00", "{}"),
    )
    _insert_ticket(memdb, "t003", status="ready")
    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))
    rows = memdb.execute("SELECT kind, target_worker FROM commands").fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "scheduler.kickoff_ready"
    assert rows[0]["target_worker"] == "orchestrator"


def test_tick_is_idempotent_for_same_seq(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'autorun_ready' WHERE id = 1")
    memdb.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-test-001", "2026-01-01T00:00:00", "{}"),
    )
    _insert_ticket(memdb, "t004", status="ready")
    worker._tick_seq = 2
    asyncio.run(worker._tick(ctx))
    asyncio.run(worker._tick(ctx))  # same tick_seq → duplicate idempotency key
    rows = memdb.execute("SELECT COUNT(*) AS n FROM commands").fetchone()
    assert rows["n"] == 1  # only one command, not two
