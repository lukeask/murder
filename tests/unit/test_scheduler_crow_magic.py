"""Unit tests for SchedulerWorker crow_magic tick logic."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from murder.scheduler.worker import SchedulerWorker
from murder.workers.base import WorkerCtx


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_ctx(db: sqlite3.Connection) -> WorkerCtx:
    return WorkerCtx(
        repo_root=None,  # type: ignore[arg-type]
        db=db,
        bus=None,
        run_id="run-cm-001",
        shutdown=None,
    )


def _insert_run(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-cm-001", "2026-01-01T00:00:00", "{}"),
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    status: str = "ready",
    wave: int = 1,
    harness: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, attempts, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, '2026-01-01', '2026-01-01')
        """,
        (ticket_id, f"title-{ticket_id}", wave, status, harness),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    harness: str,
    *,
    percent_used: float,
    t_period_minutes: float = 10_000.0,
    t_until_reset_minutes: float = 5_000.0,
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
        "fetched_at": _iso(now),
        "windows": [window],
    }
    conn.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (harness, "test", _iso(now), json.dumps(status)),
    )


def _crow_magic_mode(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE scheduler_state SET mode = 'crow_magic' WHERE id = 1")


def _command_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM commands").fetchone()["n"]


# ---------------------------------------------------------------------------
# Basic kick / no-kick decisions
# ---------------------------------------------------------------------------


def test_crow_magic_kicks_when_usage_high(memdb: sqlite3.Connection) -> None:
    """High usage (80%) in alwayscutoff zone → usage_threshold_curve returns True → command queued."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm001", harness="cursor")
    # 80% used, 5000 min left of 10000 — above alwayscutoff (0.6) → should kick
    _insert_snapshot(
        memdb, "cursor", percent_used=80.0, t_period_minutes=10_000.0, t_until_reset_minutes=5_000.0
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    rows = memdb.execute("SELECT kind, target_worker FROM commands").fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "scheduler.kickoff_ready"
    assert rows[0]["target_worker"] == "orchestrator"


def test_crow_magic_no_kick_when_usage_very_low_early(memdb: sqlite3.Connection) -> None:
    """5% usage at mid-period (t=7000) where threshold≈0.30 → usage < threshold → no kick."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm002", harness="cursor")
    # 5% used, 7000 min left of 10000 → threshold≈0.30 → 0.05 < 0.30 → no kick
    _insert_snapshot(
        memdb, "cursor", percent_used=5.0, t_period_minutes=10_000.0, t_until_reset_minutes=7_000.0
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 0


def test_crow_magic_no_kick_when_no_ready_tickets(memdb: sqlite3.Connection) -> None:
    """Snapshot passes gate but no ready tickets → no command."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm003", status="planned", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 0


def test_crow_magic_no_kick_in_manual_mode(memdb: sqlite3.Connection) -> None:
    """crow_magic tick does nothing when mode is manual."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    # mode stays manual
    _insert_run(memdb)
    _insert_ticket(memdb, "cm004", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 0


# ---------------------------------------------------------------------------
# multiharness_cutoff gate
# ---------------------------------------------------------------------------


def test_crow_magic_multiharness_cutoff_blocks_when_busy(memdb: sqlite3.Connection) -> None:
    """usage(0.3) < cutoff(0.5) AND harness is busy → skip."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)

    # Existing in-progress ticket on same harness
    _insert_ticket(memdb, "cm010", status="in_progress", harness="cursor")
    # Ready ticket
    _insert_ticket(memdb, "cm011", status="ready", harness="cursor")
    # Low usage — but usage_threshold_curve still says yes (alwayscutoff=0.6; usage=0.3 < 0.6 → False normally)
    # We need a snapshot where usage_threshold_curve says True but usage < multiharness_cutoff.
    # Use the always-yes zone: t_until_reset ≈ t_period (just entered period), threshold→0
    # Actually let's use: percent_used=70, in alwayscutoff zone (threshold=0.6), so 0.7 > 0.6 → True
    # Set multiharness_cutoff=0.8 (80%) so usage 0.7 < 0.8 and harness busy → skip
    _insert_snapshot(memdb, "cursor", percent_used=70.0)
    memdb.execute(
        """
        INSERT INTO scheduler_params(harness, window_key, c_changeoff, t_alwaysyes,
            alwayscutoff, intensity, multiharness_cutoff, updated_at)
        VALUES ('cursor', 'current_period', 0.7, 15.0, 0.6, 1.0, 0.8, '2026-01-01')
        """
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 0


def test_crow_magic_multiharness_cutoff_allows_when_not_busy(memdb: sqlite3.Connection) -> None:
    """usage(0.7) < cutoff(0.8) BUT harness is not busy → still kick."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm012", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=70.0)
    memdb.execute(
        """
        INSERT INTO scheduler_params(harness, window_key, c_changeoff, t_alwaysyes,
            alwayscutoff, intensity, multiharness_cutoff, updated_at)
        VALUES ('cursor', 'current_period', 0.7, 15.0, 0.6, 1.0, 0.8, '2026-01-01')
        """
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 1


def test_crow_magic_multiharness_cutoff_null_disabled(memdb: sqlite3.Connection) -> None:
    """multiharness_cutoff=NULL → gate disabled, kick regardless of in-progress count."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm013", status="in_progress", harness="cursor")
    _insert_ticket(memdb, "cm014", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=70.0)
    memdb.execute(
        """
        INSERT INTO scheduler_params(harness, window_key, c_changeoff, t_alwaysyes,
            alwayscutoff, intensity, multiharness_cutoff, updated_at)
        VALUES ('cursor', 'current_period', 0.7, 15.0, 0.6, 1.0, NULL, '2026-01-01')
        """
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 1


# ---------------------------------------------------------------------------
# t_period derivation
# ---------------------------------------------------------------------------


def test_crow_magic_skips_window_without_period(memdb: sqlite3.Connection) -> None:
    """Window with no starts_at and unrecognised name → t_period=None → skip."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm020", harness="cursor")
    now = _now_utc()
    reset_at = now + timedelta(hours=2)
    window = {
        "name": "current_session",  # no starts_at, name not parseable as "Nh"/"Nd"
        "percent_used": 80.0,
        "reset_at": _iso(reset_at),
    }
    status = {"harness": "cursor", "source": "test", "fetched_at": _iso(now), "windows": [window]}
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?,?,?,?)",
        ("cursor", "test", _iso(now), json.dumps(status)),
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 0


def test_crow_magic_uses_named_window_period(memdb: sqlite3.Connection) -> None:
    """Window name '5h' → t_period=300 minutes → decision proceeds normally."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm021", harness="codex")
    now = _now_utc()
    # 5h period, 290 min until reset → x=290 ≥ B_eff=285 → always-yes zone → kick
    # (B_eff = t_period - t_alwaysyes = 300 - 15 = 285)
    reset_at = now + timedelta(minutes=290)
    window = {
        "name": "5h",
        "percent_used": 10.0,  # any usage passes in always-yes zone
        "reset_at": _iso(reset_at),
    }
    status = {"harness": "codex", "source": "test", "fetched_at": _iso(now), "windows": [window]}
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?,?,?,?)",
        ("codex", "test", _iso(now), json.dumps(status)),
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    assert _command_count(memdb) == 1


# ---------------------------------------------------------------------------
# Idempotency within a tick
# ---------------------------------------------------------------------------


def test_crow_magic_tick_idempotent(memdb: sqlite3.Connection) -> None:
    """Same tick_seq → duplicate idempotency key → second call inserts nothing."""
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm030", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=80.0)

    worker._tick_seq = 5
    asyncio.run(worker._tick(ctx))
    asyncio.run(worker._tick(ctx))  # same seq → collision → silently ignored

    assert _command_count(memdb) == 1


# ---------------------------------------------------------------------------
# scheduler.set_params RPC
# ---------------------------------------------------------------------------


def test_set_params_upserts_row(memdb: sqlite3.Connection) -> None:
    from murder.bus.protocol import CommandEvent

    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = CommandEvent(
        run_id="run-cm-001",
        agent_id="tui",
        target_worker="scheduler",
        kind="scheduler.set_params",
        payload={
            "harness": "cursor",
            "window_key": "current_period",
            "params": {"c_changeoff": 0.5, "alwayscutoff": 0.7, "multiharness_cutoff": 0.6},
        },
        correlation_id="c1",
        idempotency_key="i1",
    )
    result = asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))
    assert result["handled"] is True
    row = memdb.execute(
        "SELECT c_changeoff, alwayscutoff, multiharness_cutoff FROM scheduler_params "
        "WHERE harness = 'cursor' AND window_key = 'current_period'"
    ).fetchone()
    assert row is not None
    assert row["c_changeoff"] == 0.5
    assert row["alwayscutoff"] == 0.7
    assert row["multiharness_cutoff"] == 0.6


def test_set_params_is_idempotent(memdb: sqlite3.Connection) -> None:
    from murder.bus.protocol import CommandEvent

    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    payload = {
        "harness": "cursor",
        "window_key": "5h",
        "params": {"alwayscutoff": 0.5},
    }
    for i in range(3):
        cmd = CommandEvent(
            run_id="run-cm-001",
            agent_id="tui",
            target_worker="scheduler",
            kind="scheduler.set_params",
            payload=payload,
            correlation_id=f"c{i}",
            idempotency_key=f"i{i}",
        )
        asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))

    count = memdb.execute("SELECT COUNT(*) AS n FROM scheduler_params").fetchone()["n"]
    assert count == 1


def test_set_params_coerces_numeric_and_null_cutoff(memdb: sqlite3.Connection) -> None:
    from murder.bus.protocol import CommandEvent

    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = CommandEvent(
        run_id="run-cm-001",
        agent_id="tui",
        target_worker="scheduler",
        kind="scheduler.set_params",
        payload={
            "harness": "cursor",
            "window_key": "7d",
            "params": {"multiharness_cutoff": "80"},
        },
        correlation_id="c1",
        idempotency_key="i1",
    )
    asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))

    row = memdb.execute(
        "SELECT multiharness_cutoff FROM scheduler_params WHERE harness='cursor' AND window_key='7d'"
    ).fetchone()
    assert row is not None
    assert row["multiharness_cutoff"] == 80.0

    cmd_null = CommandEvent(
        run_id="run-cm-001",
        agent_id="tui",
        target_worker="scheduler",
        kind="scheduler.set_params",
        payload={
            "harness": "cursor",
            "window_key": "7d",
            "params": {"multiharness_cutoff": None},
        },
        correlation_id="c2",
        idempotency_key="i2",
    )
    asyncio.run(worker.on_command(cmd_null, _make_ctx(memdb)))
    row2 = memdb.execute(
        "SELECT multiharness_cutoff FROM scheduler_params WHERE harness='cursor' AND window_key='7d'"
    ).fetchone()
    assert row2 is not None
    assert row2["multiharness_cutoff"] is None


def test_set_params_rejects_invalid_payload(memdb: sqlite3.Connection) -> None:
    from murder.bus.protocol import CommandEvent

    worker = SchedulerWorker()
    asyncio.run(worker.on_start(_make_ctx(memdb)))
    cmd = CommandEvent(
        run_id="run-cm-001",
        agent_id="tui",
        target_worker="scheduler",
        kind="scheduler.set_params",
        payload={
            "harness": "cursor",
            "window_key": "5h",
            "params": {"multiharness_cutoff": "not-a-number"},
        },
        correlation_id="c1",
        idempotency_key="i1",
    )
    with pytest.raises(ValueError, match="scheduler.set_params: invalid payload"):
        asyncio.run(worker.on_command(cmd, _make_ctx(memdb)))


def test_crow_magic_ignores_bad_stored_cutoff_without_type_error(memdb: sqlite3.Connection) -> None:
    worker = SchedulerWorker()
    ctx = _make_ctx(memdb)
    asyncio.run(worker.on_start(ctx))
    _crow_magic_mode(memdb)
    _insert_run(memdb)
    _insert_ticket(memdb, "cm040", status="in_progress", harness="cursor")
    _insert_ticket(memdb, "cm041", status="ready", harness="cursor")
    _insert_snapshot(memdb, "cursor", percent_used=70.0)
    # Simulate bad historic data persisted before validation existed.
    memdb.execute(
        """
        INSERT INTO scheduler_params(harness, window_key, c_changeoff, t_alwaysyes,
            alwayscutoff, intensity, multiharness_cutoff, updated_at)
        VALUES ('cursor', 'current_period', 0.7, 15.0, 0.6, 1.0, 'bad', '2026-01-01')
        """
    )

    worker._tick_seq = 1
    asyncio.run(worker._tick(ctx))

    # With invalid cutoff ignored, decision path should still enqueue once.
    assert _command_count(memdb) == 1
