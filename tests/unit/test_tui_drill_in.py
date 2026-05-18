"""Tests for GaugeDrillIn helpers: sparkline, reset detection, burn attribution."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import murder.db as dbmod
from murder.tui.dispatch.gauges import (
    GaugeDrillIn,
    GaugeStrip,
    _burn_attribution,
    _GaugeData,
    _recent_reset_events,
    _spark_history,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    dbmod.init_schema(conn)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _insert_snapshot(
    conn: sqlite3.Connection,
    harness: str,
    pct: float,
    window_key: str = "5h",
    fetched_at: str | None = None,
    t_until_minutes: float = 120.0,
) -> None:
    now = _now()
    reset_at = now + timedelta(minutes=t_until_minutes)
    window = {
        "name": window_key,
        "percent_used": pct,
        "starts_at": (now - timedelta(hours=5)).isoformat(),
        "ends_at": reset_at.isoformat(),
        "reset_at": reset_at.isoformat(),
    }
    payload = {
        "harness": harness,
        "source": "test",
        "fetched_at": fetched_at or _iso(now),
        "windows": [window],
    }
    conn.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (harness, "test", fetched_at or _iso(now), json.dumps(payload)),
    )


def _insert_agent(
    conn: sqlite3.Connection,
    agent_id: str,
    ticket_id: str,
    started_at: str,
    last_heartbeat_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agents(agent_id, role, ticket_id, session, status,
                           start_commit, started_at, last_heartbeat_at, pid)
        VALUES (?, 'crow', ?, NULL, 'running', NULL, ?, ?, NULL)
        """,
        (agent_id, ticket_id, started_at, last_heartbeat_at),
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    harness: str = "cursor",
    title: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, harness, attempts, created_at, updated_at)
        VALUES (?, ?, 1, 'in_progress', ?, 0, '2026-05-01', '2026-05-01')
        """,
        (ticket_id, title or f"Title {ticket_id}", harness),
    )


# ---------------------------------------------------------------------------
# _spark_history
# ---------------------------------------------------------------------------


def test_spark_history_empty_db() -> None:
    db = _make_db()
    result = _spark_history(db, "cursor", "5h")
    assert result == "(no history)"


def test_spark_history_returns_14_chars_with_data() -> None:
    db = _make_db()
    now = _now()
    for i in range(5):
        ts = _iso(now - timedelta(days=i, hours=1))
        _insert_snapshot(db, "cursor", 50.0, "5h", fetched_at=ts)
    result = _spark_history(db, "cursor", "5h")
    assert len(result) == 14


def test_spark_history_high_usage_produces_filled_bars() -> None:
    db = _make_db()
    now = _now()
    # 100% today
    _insert_snapshot(db, "cursor", 100.0, "5h", fetched_at=_iso(now))
    result = _spark_history(db, "cursor", "5h")
    assert "█" in result


def test_spark_history_zero_usage_produces_empty_bars() -> None:
    db = _make_db()
    now = _now()
    _insert_snapshot(db, "cursor", 0.0, "5h", fetched_at=_iso(now))
    result = _spark_history(db, "cursor", "5h")
    assert "▁" in result


def test_spark_history_ignores_different_window_key() -> None:
    db = _make_db()
    now = _now()
    _insert_snapshot(db, "cursor", 80.0, "7d", fetched_at=_iso(now))
    # Query for "5h" window — no data
    result = _spark_history(db, "cursor", "5h")
    assert result == "(no history)"


# ---------------------------------------------------------------------------
# _recent_reset_events
# ---------------------------------------------------------------------------


def test_recent_resets_empty_db() -> None:
    db = _make_db()
    result = _recent_reset_events(db, "cursor", "5h")
    assert result == []


def test_recent_resets_detects_drop_from_60_to_3() -> None:
    db = _make_db()
    now = _now()
    _insert_snapshot(db, "cursor", 60.0, "5h", fetched_at=_iso(now - timedelta(hours=2)))
    _insert_snapshot(db, "cursor", 3.0, "5h", fetched_at=_iso(now - timedelta(hours=1)))
    result = _recent_reset_events(db, "cursor", "5h")
    assert len(result) == 1
    assert result[0]["peak_pct"] == 60.0


def test_recent_resets_no_detection_for_small_drop() -> None:
    db = _make_db()
    now = _now()
    _insert_snapshot(db, "cursor", 50.0, "5h", fetched_at=_iso(now - timedelta(hours=2)))
    _insert_snapshot(db, "cursor", 40.0, "5h", fetched_at=_iso(now - timedelta(hours=1)))
    result = _recent_reset_events(db, "cursor", "5h")
    assert result == []


def test_recent_resets_only_matches_correct_window() -> None:
    db = _make_db()
    now = _now()
    # Insert a 7d window reset, query for 5h — should not match
    now_ts = _now()
    reset_at = now_ts + timedelta(hours=120)

    def _insert_7d(pct: float, ts: str) -> None:
        window = {
            "name": "7d",
            "percent_used": pct,
            "starts_at": (now_ts - timedelta(days=7)).isoformat(),
            "ends_at": reset_at.isoformat(),
            "reset_at": reset_at.isoformat(),
        }
        payload = {"harness": "cursor", "source": "test", "fetched_at": ts, "windows": [window]}
        db.execute(
            "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
            ("cursor", "test", ts, json.dumps(payload)),
        )

    _insert_7d(70.0, _iso(now - timedelta(hours=2)))
    _insert_7d(2.0, _iso(now - timedelta(hours=1)))
    result = _recent_reset_events(db, "cursor", "5h")
    assert result == []


def test_recent_resets_returns_reset_at_timestamp() -> None:
    db = _make_db()
    now = _now()
    ts_before = _iso(now - timedelta(hours=3))
    ts_after = _iso(now - timedelta(hours=2))
    _insert_snapshot(db, "cursor", 80.0, "5h", fetched_at=ts_before)
    _insert_snapshot(db, "cursor", 2.0, "5h", fetched_at=ts_after)
    result = _recent_reset_events(db, "cursor", "5h")
    assert len(result) == 1
    assert result[0]["reset_at"] == ts_after


# ---------------------------------------------------------------------------
# _burn_attribution
# ---------------------------------------------------------------------------


def test_burn_attribution_empty_db() -> None:
    db = _make_db()
    result = _burn_attribution(db, "cursor", 300.0)
    assert result == []


def test_burn_attribution_zero_period() -> None:
    db = _make_db()
    result = _burn_attribution(db, "cursor", 0.0)
    assert result == []


def test_burn_attribution_active_agent_in_window() -> None:
    db = _make_db()
    now = _now()
    _insert_ticket(db, "t001", harness="cursor", title="Ticket one")
    started = _iso(now - timedelta(minutes=90))
    heartbeat = _iso(now - timedelta(minutes=10))
    _insert_agent(db, "ag001", "t001", started, heartbeat)

    result = _burn_attribution(db, "cursor", 300.0)
    assert len(result) == 1
    assert result[0]["ticket_id"] == "t001"
    assert result[0]["active_minutes"] > 0


def test_burn_attribution_agent_outside_window_excluded() -> None:
    db = _make_db()
    now = _now()
    _insert_ticket(db, "t002", harness="cursor", title="Old ticket")
    # Agent ran 400+ minutes ago, window is 300 minutes
    started = _iso(now - timedelta(minutes=500))
    heartbeat = _iso(now - timedelta(minutes=410))
    _insert_agent(db, "ag002", "t002", started, heartbeat)

    result = _burn_attribution(db, "cursor", 300.0)
    assert result == []


def test_burn_attribution_sorted_by_active_minutes() -> None:
    db = _make_db()
    now = _now()
    _insert_ticket(db, "t003", harness="cursor", title="Short run")
    _insert_ticket(db, "t004", harness="cursor", title="Long run")

    # t004 runs 120 min, t003 runs 30 min
    _insert_agent(
        db, "ag003", "t003", _iso(now - timedelta(minutes=40)), _iso(now - timedelta(minutes=10))
    )
    _insert_agent(
        db, "ag004", "t004", _iso(now - timedelta(minutes=130)), _iso(now - timedelta(minutes=10))
    )

    result = _burn_attribution(db, "cursor", 300.0)
    assert len(result) == 2
    assert result[0]["ticket_id"] == "t004"
    assert result[1]["ticket_id"] == "t003"


def test_burn_attribution_harness_filter() -> None:
    db = _make_db()
    now = _now()
    _insert_ticket(db, "t005", harness="cursor", title="Cursor ticket")
    _insert_ticket(db, "t006", harness="codex", title="Codex ticket")
    _insert_agent(db, "ag005", "t005", _iso(now - timedelta(minutes=60)), _iso(now))
    _insert_agent(db, "ag006", "t006", _iso(now - timedelta(minutes=60)), _iso(now))

    cursor_result = _burn_attribution(db, "cursor", 300.0)
    codex_result = _burn_attribution(db, "codex", 300.0)
    assert all(r["ticket_id"] == "t005" for r in cursor_result)
    assert all(r["ticket_id"] == "t006" for r in codex_result)


# ---------------------------------------------------------------------------
# GaugeDrillIn._build_content
# ---------------------------------------------------------------------------


def test_drill_in_build_content_has_sections() -> None:
    db = _make_db()
    g = _GaugeData(
        harness="cursor",
        window_key="5h",
        pct=75.0,
        t_until_reset_minutes=120.0,
        t_period_minutes=300.0,
    )
    modal = GaugeDrillIn(g, db)
    content = modal._build_content()
    assert "14-day history" in content
    assert "Recent resets" in content
    assert "What burned this period" in content


def test_drill_in_build_content_shows_pct() -> None:
    db = _make_db()
    g = _GaugeData(
        harness="cursor",
        window_key="5h",
        pct=73.0,
        t_until_reset_minutes=90.0,
        t_period_minutes=300.0,
    )
    modal = GaugeDrillIn(g, db)
    content = modal._build_content()
    assert "73%" in content


def test_drill_in_build_content_shows_rst() -> None:
    db = _make_db()
    g = _GaugeData(
        harness="cursor",
        window_key="5h",
        pct=50.0,
        t_until_reset_minutes=90.0,
        t_period_minutes=300.0,
    )
    modal = GaugeDrillIn(g, db)
    content = modal._build_content()
    assert "1h30m" in content


def test_drill_in_build_content_lists_burn_ticket() -> None:
    db = _make_db()
    now = _now()
    _insert_ticket(db, "t010", harness="cursor", title="Burned ticket")
    _insert_agent(db, "ag010", "t010", _iso(now - timedelta(minutes=60)), _iso(now))

    g = _GaugeData(
        harness="cursor",
        window_key="5h",
        pct=50.0,
        t_until_reset_minutes=90.0,
        t_period_minutes=300.0,
    )
    modal = GaugeDrillIn(g, db)
    content = modal._build_content()
    assert "t010" in content
    assert "Burned ticket" in content


def test_drill_in_build_content_lists_reset_event() -> None:
    db = _make_db()
    now = _now()
    _insert_snapshot(db, "cursor", 70.0, "5h", fetched_at=_iso(now - timedelta(hours=3)))
    _insert_snapshot(db, "cursor", 2.0, "5h", fetched_at=_iso(now - timedelta(hours=2)))

    g = _GaugeData(
        harness="cursor",
        window_key="5h",
        pct=2.0,
        t_until_reset_minutes=90.0,
        t_period_minutes=300.0,
    )
    modal = GaugeDrillIn(g, db)
    content = modal._build_content()
    assert "70%" in content
    assert "reset on" in content


# ---------------------------------------------------------------------------
# GaugeStrip drill-in wiring
# ---------------------------------------------------------------------------


def test_gauge_strip_stores_db_on_refresh() -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0)
    strip = GaugeStrip()
    strip.refresh_from_db(db)
    assert strip._db is db


def test_gauge_strip_drill_in_no_crash_without_gauges() -> None:
    strip = GaugeStrip()
    # No app — action should be a no-op, not crash
    strip.action_drill_in()


# ---------------------------------------------------------------------------
# SchedulerWorker prune (unit-level)
# ---------------------------------------------------------------------------


def test_prune_removes_old_snapshots(memdb: sqlite3.Connection) -> None:
    from murder.scheduler.worker import SchedulerWorker

    now = _now()
    # Insert old snapshot (>60 days)
    old_ts = _iso(now - timedelta(days=61))
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (
            "cursor",
            "test",
            old_ts,
            json.dumps(
                {"harness": "cursor", "source": "test", "fetched_at": old_ts, "windows": []}
            ),
        ),
    )
    # Insert recent snapshot
    recent_ts = _iso(now - timedelta(days=1))
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (
            "cursor",
            "test",
            recent_ts,
            json.dumps(
                {"harness": "cursor", "source": "test", "fetched_at": recent_ts, "windows": []}
            ),
        ),
    )

    worker = SchedulerWorker()
    worker._prune_old_snapshots(memdb)

    remaining = memdb.execute("SELECT fetched_at FROM harness_usage_snapshots").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["fetched_at"] == recent_ts


def test_prune_on_start_cleans_old_data(memdb: sqlite3.Connection) -> None:
    import asyncio

    from murder.scheduler.worker import SchedulerWorker
    from murder.workers.base import WorkerCtx

    now = _now()
    old_ts = _iso(now - timedelta(days=90))
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        ("cursor", "test", old_ts, json.dumps({"harness": "cursor", "windows": []})),
    )

    worker = SchedulerWorker()
    ctx = WorkerCtx(repo_root=None, db=memdb, bus=None, run_id="run-prune-001", shutdown=None)  # type: ignore[arg-type]
    asyncio.run(worker.on_start(ctx))

    count = memdb.execute("SELECT COUNT(*) AS n FROM harness_usage_snapshots").fetchone()["n"]
    assert count == 0
