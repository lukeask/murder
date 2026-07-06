from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from murder.bus.broker import DurableBroker
from murder.bus.protocol import EventFilter
from murder.state.persistence.event_log import insert_event
from murder.state.persistence.schema import init_db


class _NoopBus:
    async def publish(self, event: Any) -> None:
        pass


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO runs(run_id, started_at, config_snapshot)
        VALUES ('run-test', '2026-07-06T00:00:00', '{}')
        """
    )
    return conn


def _insert_heartbeat(
    conn: sqlite3.Connection,
    *,
    agent_id: str = "agent-1",
    ticket_id: str | None = None,
    ts: str = "2026-07-06T00:00:00",
) -> int:
    return insert_event(
        conn,
        run_id="run-test",
        agent_id=agent_id,
        role="crow",
        ticket_id=ticket_id,
        type="heartbeat",
        payload={"state": "progressing", "summary": "ok", "since_change_s": 0},
        ts=ts,
    )


def _insert_error(
    conn: sqlite3.Connection,
    *,
    agent_id: str = "agent-1",
    ts: str = "2026-07-06T00:00:00",
) -> int:
    return insert_event(
        conn,
        run_id="run-test",
        agent_id=agent_id,
        role="crow",
        ticket_id=None,
        type="error",
        payload={"message": "boom", "recoverable": True},
        ts=ts,
    )


def test_replay_type_filter_matches_python_filter_for_valid_rows() -> None:
    conn = _conn()
    first = _insert_heartbeat(conn, agent_id="agent-1", ticket_id="t1")
    _insert_error(conn, agent_id="agent-1")
    _insert_heartbeat(conn, agent_id="agent-2", ticket_id="t2")
    fourth = _insert_heartbeat(conn, agent_id="agent-1", ticket_id="t3")

    broker = DurableBroker(_NoopBus(), conn)
    filt = EventFilter(type="heartbeat", agent_id="agent-1")

    python_filtered = [
        (row_id, event)
        for row_id, event in broker.replay(None, since_id=0)
        if filt.matches(event)
    ]
    sql_filtered = broker.replay(filt, since_id=0)

    assert [row_id for row_id, _ in sql_filtered] == [first, fourth]
    assert [row_id for row_id, _ in sql_filtered] == [
        row_id for row_id, _ in python_filtered
    ]


def test_replay_exact_type_filter_skips_nonmatching_json_decode() -> None:
    conn = _conn()
    good_id = _insert_heartbeat(conn)
    conn.execute(
        """
        INSERT INTO events(
            ts, run_id, agent_id, role, ticket_id, type, schema_version, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-07-06T00:00:01",
            "run-test",
            "agent-1",
            "crow",
            None,
            "error",
            1,
            "{not valid json",
        ),
    )

    broker = DurableBroker(_NoopBus(), conn)

    rows = broker.replay(EventFilter(type="heartbeat"), since_id=0)

    assert [row_id for row_id, _ in rows] == [good_id]


def test_durable_broker_ensures_type_id_replay_index() -> None:
    conn = _conn()
    DurableBroker(_NoopBus(), conn)

    row = conn.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'index'
           AND name = 'idx_events_type_id'
        """
    ).fetchone()

    assert row is not None


def test_cursor_retention_helpers_track_oldest_and_watermark() -> None:
    conn = _conn()
    _insert_heartbeat(conn)
    second = _insert_error(conn)

    broker = DurableBroker(_NoopBus(), conn)

    assert broker.current_max_id() == second
    assert broker.watermark() == second
    assert broker.oldest_event_id() == 1
    assert broker.is_cursor_retained(0) is True
    assert broker.is_cursor_retained(1) is True
    assert broker.is_cursor_retained(second) is True
    assert broker.is_cursor_retained(second + 1) is False
    assert broker.is_cursor_retained(-1) is False


def test_prune_retained_events_batches_repeated_oldest_delete_algorithm() -> None:
    conn = _conn()
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    old = (now - timedelta(days=8)).isoformat(timespec="seconds")
    fresh = (now - timedelta(days=1)).isoformat(timespec="seconds")

    for _ in range(4):
        _insert_heartbeat(conn, ts=old)
    for _ in range(2):
        _insert_heartbeat(conn, ts=fresh)

    broker = DurableBroker(
        _NoopBus(),
        conn,
        retention_min_events=3,
        retention_max_age_days=7,
    )

    deleted = broker.prune_retained_events(now=now)

    remaining_ids = [
        int(row["id"])
        for row in conn.execute("SELECT id FROM events ORDER BY id ASC").fetchall()
    ]
    assert deleted == 3
    assert remaining_ids == [4, 5, 6]
    assert broker.oldest_event_id() == 4
    assert broker.is_cursor_retained(2) is False
    assert broker.is_cursor_retained(3) is True
