"""Tests for SchedulerWorker decision events and usage.reset detection."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from murder.bus.protocol import SchedulerDecisionEvent, UsageResetEvent
from murder.scheduler.worker import SchedulerWorker
from murder.workers.base import WorkerCtx


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_ctx(db: sqlite3.Connection, bus=None) -> WorkerCtx:
    return WorkerCtx(
        repo_root=None,  # type: ignore[arg-type]
        db=db,
        bus=bus,
        run_id="run-de-001",
        shutdown=None,
    )


def _insert_run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-de-001", "2026-01-01T00:00:00", "{}"),
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    status: str = "ready",
    harness: str | None = "cursor",
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, attempts, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?, 0, '2026-01-01', '2026-01-01')
        """,
        (ticket_id, f"title-{ticket_id}", status, harness),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    harness: str,
    *,
    percent_used: float,
    t_period_minutes: float = 10_000.0,
    t_until_reset_minutes: float = 5_000.0,
    fetched_at: str | None = None,
) -> None:
    now = _now_utc()
    starts_at = now - timedelta(minutes=t_period_minutes - t_until_reset_minutes)
    ends_at = now + timedelta(minutes=t_until_reset_minutes)
    window = {
        "name": "current_period",
        "percent_used": percent_used,
        "starts_at": _iso(starts_at),
        "ends_at": _iso(ends_at),
        "reset_at": _iso(ends_at),
    }
    status = {
        "harness": harness,
        "source": "test",
        "fetched_at": fetched_at or _iso(now),
        "windows": [window],
    }
    conn.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (harness, "test", fetched_at or _iso(now), json.dumps(status)),
    )


# ---------------------------------------------------------------------------
# Rationale format
# ---------------------------------------------------------------------------


def test_hold_rationale_format(memdb: sqlite3.Connection) -> None:
    """Hold decision produces 'Holding: ...' rationale in decision cache."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    _insert_ticket(memdb, "de001", status="ready", harness="cursor")
    # 5% usage at t=7000 → threshold ≈ 0.30 → hold
    _insert_snapshot(
        memdb, "cursor", percent_used=5.0, t_period_minutes=10_000.0, t_until_reset_minutes=7_000.0
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    row = memdb.execute(
        "SELECT * FROM scheduler_decision_cache WHERE harness = 'cursor'"
    ).fetchone()
    assert row is not None
    assert row["decision"] == 0
    assert "Holding" in row["rationale"]
    assert "cursor" in row["rationale"]
    assert "5%" in row["rationale"]


def test_kick_rationale_format(memdb: sqlite3.Connection) -> None:
    """Kick decision produces 'Kicking ...' rationale with ticket id."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    _insert_ticket(memdb, "de002", status="ready", harness="cursor")
    # 80% usage → threshold ≈ 0.60 → kick
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    row = memdb.execute(
        "SELECT * FROM scheduler_decision_cache WHERE harness = 'cursor'"
    ).fetchone()
    assert row is not None
    assert row["decision"] == 1
    assert "Kicking" in row["rationale"]
    assert "de002" in row["rationale"]


def test_no_ready_tickets_rationale(memdb: sqlite3.Connection) -> None:
    """No ready tickets → cache row with decision=0 and 'No ready tickets' rationale."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    # No tickets inserted
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    row = memdb.execute(
        "SELECT * FROM scheduler_decision_cache WHERE harness = 'cursor'"
    ).fetchone()
    assert row is not None
    assert row["decision"] == 0
    assert "No ready tickets" in row["rationale"]


# ---------------------------------------------------------------------------
# Decision event emission
# ---------------------------------------------------------------------------


def test_decision_event_emitted_on_hold(memdb: sqlite3.Connection) -> None:
    """Hold decision emits a SchedulerDecisionEvent with decision=False."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb, bus=bus)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    _insert_ticket(memdb, "de010", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=5.0, t_until_reset_minutes=7_000.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert bus.publish.call_count >= 1
    events = [c.args[0] for c in bus.publish.call_args_list]
    decision_events = [e for e in events if isinstance(e, SchedulerDecisionEvent)]
    assert len(decision_events) >= 1
    ev = decision_events[0]
    assert ev.decision is False
    assert ev.harness == "cursor"
    assert "Holding" in ev.rationale


def test_decision_event_emitted_on_kick(memdb: sqlite3.Connection) -> None:
    """Kick decision emits a SchedulerDecisionEvent with decision=True."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb, bus=bus)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    _insert_ticket(memdb, "de011", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    events = [c.args[0] for c in bus.publish.call_args_list]
    decision_events = [e for e in events if isinstance(e, SchedulerDecisionEvent)]
    assert len(decision_events) >= 1
    ev = decision_events[0]
    assert ev.decision is True
    assert ev.kicked_ticket_id == "de011"


# ---------------------------------------------------------------------------
# Usage-reset detection
# ---------------------------------------------------------------------------


def _insert_snapshot_raw(
    conn: sqlite3.Connection,
    harness: str,
    percent_used: float,
    fetched_at: str,
    t_period_minutes: float = 10_000.0,
    t_until_reset_minutes: float = 5_000.0,
) -> None:
    """Insert snapshot with explicit fetched_at for ordering."""
    now = datetime.fromisoformat(fetched_at)
    starts_at = now - timedelta(minutes=t_period_minutes - t_until_reset_minutes)
    ends_at = now + timedelta(minutes=t_until_reset_minutes)
    window = {
        "name": "current_period",
        "percent_used": percent_used,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "reset_at": ends_at.isoformat(),
    }
    status = {
        "harness": harness,
        "source": "test",
        "fetched_at": fetched_at,
        "windows": [window],
    }
    conn.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (harness, "test", fetched_at, json.dumps(status)),
    )


def test_usage_reset_emitted_when_usage_drops(memdb: sqlite3.Connection) -> None:
    """When prev≥30% and curr≤5%, UsageResetEvent is emitted."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb, bus=bus)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)

    now = _now_utc()
    _insert_snapshot_raw(memdb, "cursor", 60.0, _iso(now - timedelta(minutes=10)))
    _insert_snapshot_raw(memdb, "cursor", 3.0, _iso(now))

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    events = [c.args[0] for c in bus.publish.call_args_list]
    reset_events = [e for e in events if isinstance(e, UsageResetEvent)]
    assert len(reset_events) == 1
    ev = reset_events[0]
    assert ev.harness == "cursor"
    assert ev.prev_pct == 60.0
    assert ev.curr_pct == 3.0


def test_usage_reset_not_emitted_when_drop_too_small(memdb: sqlite3.Connection) -> None:
    """Small drop (50%→40%) does NOT emit UsageResetEvent."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb, bus=bus)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)

    now = _now_utc()
    _insert_snapshot_raw(memdb, "cursor", 50.0, _iso(now - timedelta(minutes=10)))
    _insert_snapshot_raw(memdb, "cursor", 40.0, _iso(now))

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    events = [c.args[0] for c in bus.publish.call_args_list]
    reset_events = [e for e in events if isinstance(e, UsageResetEvent)]
    assert len(reset_events) == 0


def test_usage_reset_not_emitted_twice_for_same_peak(memdb: sqlite3.Connection) -> None:
    """Second tick with same prev snapshot does not double-emit."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb, bus=bus)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)

    now = _now_utc()
    _insert_snapshot_raw(memdb, "cursor", 60.0, _iso(now - timedelta(minutes=10)))
    _insert_snapshot_raw(memdb, "cursor", 3.0, _iso(now))

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))
    worker._tick_seq = 2
    asyncio.run(worker._tick(ctx))

    events = [c.args[0] for c in bus.publish.call_args_list]
    reset_events = [e for e in events if isinstance(e, UsageResetEvent)]
    assert len(reset_events) == 1


def test_usage_reset_not_emitted_without_bus(memdb: sqlite3.Connection) -> None:
    """No bus → usage.reset detection runs silently (no crash)."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)  # bus=None
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)

    now = _now_utc()
    _insert_snapshot_raw(memdb, "cursor", 60.0, _iso(now - timedelta(minutes=10)))
    _insert_snapshot_raw(memdb, "cursor", 3.0, _iso(now))

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))  # should not raise


# ---------------------------------------------------------------------------
# Decision cache upsert
# ---------------------------------------------------------------------------


def test_decision_cache_upserted_on_every_tick(memdb: sqlite3.Connection) -> None:
    """Cache row is upserted on each tick; only one row exists per (harness, window_key)."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    memdb.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")
    _insert_run(memdb)
    _insert_ticket(memdb, "de020", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=5.0, t_until_reset_minutes=7_000.0)

    for seq in range(1, 4):
        worker._tick_seq = seq
        asyncio.run(worker._tick(ctx))

    count = memdb.execute(
        "SELECT COUNT(*) AS n FROM scheduler_decision_cache WHERE harness = 'cursor'"
    ).fetchone()["n"]
    assert count == 1
