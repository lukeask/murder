"""Planner session sweeper: reclaim orphaned planner/planning_handler tmux sessions.

Covers the persistence predicate (list_orphaned_planner_sessions) and one worker
sweep iteration. Live planners on draft/accepted plans are never swept.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.planner_session_sweeper import PlannerSessionSweeperWorker
from murder.state.persistence.agents import list_orphaned_planner_sessions
from murder.state.persistence.schema import get_db, init_db


def _db():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    return conn


def _insert_agent(
    conn,
    agent_id: str,
    role: str,
    status: str,
    session: str | None,
    *,
    started_at: str = "2026-01-01",
) -> None:
    conn.execute(
        "INSERT INTO agents(agent_id, role, status, session, started_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (agent_id, role, status, session, started_at),
    )


def _insert_plan(conn, name: str, status: str, *, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO plans(name, status, created_at, updated_at, body, body_hash,
                          materialized_path)
        VALUES (?, ?, '2026-01-01', ?, '', 'h', ?)
        """,
        (name, status, updated_at, f".murder/plans/{name}.md"),
    )


def _ids(rows):
    return {r["agent_id"] for r in rows}


def test_terminal_planner_with_session_returned_regardless_of_age():
    conn = _db()
    # Plan still exists & accepted, but the planner agent itself is terminal.
    _insert_plan(conn, "p1", "accepted", updated_at="2026-01-01")
    _insert_agent(conn, "planner-p1", "planner", "dead", "planner-p1-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == {"planner-p1"}


def test_superseded_plan_older_than_threshold_returned():
    conn = _db()
    _insert_plan(conn, "p2", "superseded", updated_at="2000-01-01")
    _insert_agent(conn, "planner-p2", "planner", "running", "planner-p2-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == {"planner-p2"}


def test_superseded_plan_recent_not_returned():
    conn = _db()
    # updated_at = now → newer than 30 min ago → not yet sweepable.
    now = conn.execute("SELECT datetime('now') AS n").fetchone()["n"]
    _insert_plan(conn, "p3", "superseded", updated_at=now)
    _insert_agent(conn, "planner-p3", "planner", "running", "planner-p3-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == set()


def test_live_planner_on_draft_or_accepted_plan_not_returned():
    conn = _db()
    _insert_plan(conn, "p4", "draft", updated_at="2000-01-01")
    _insert_agent(conn, "planner-p4", "planner", "running", "planner-p4-sess")
    _insert_plan(conn, "p5", "accepted", updated_at="2000-01-01")
    _insert_agent(conn, "planner-p5", "planner", "idle", "planner-p5-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == set()


def test_planning_handler_follows_same_rules():
    conn = _db()
    # Missing plan + old agent started_at → sweepable.
    _insert_agent(
        conn,
        "planning_handler-p6",
        "planning_handler",
        "running",
        "ph-p6-sess",
        started_at="2000-01-01",
    )
    # Live accepted plan → not sweepable.
    _insert_plan(conn, "p7", "accepted", updated_at="2000-01-01")
    _insert_agent(conn, "planning_handler-p7", "planning_handler", "running", "ph-p7-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == {"planning_handler-p6"}


def test_iso_t_timestamps_compare_correctly():
    """Real rows store ISO-T timestamps ('2026-06-11T17:00:00'); SQLite's
    datetime('now', ...) is space-separated. Raw string comparison would make
    same-day anchors never 'old' ('T' > ' '); the predicate must normalize."""
    conn = _db()
    # Anchor 2 hours ago in ISO-T form → older than 30 min → sweepable.
    old_iso_t = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S', datetime('now', '-2 hours')) AS t"
    ).fetchone()["t"]
    _insert_plan(conn, "p9", "superseded", updated_at=old_iso_t)
    _insert_agent(conn, "planner-p9", "planner", "running", "planner-p9-sess")
    # Anchor just now in ISO-T form → NOT sweepable.
    now_iso_t = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S', 'now') AS t"
    ).fetchone()["t"]
    _insert_plan(conn, "p10", "superseded", updated_at=now_iso_t)
    _insert_agent(conn, "planner-p10", "planner", "running", "planner-p10-sess")

    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert _ids(rows) == {"planner-p9"}


def test_null_session_rows_never_returned():
    conn = _db()
    _insert_agent(conn, "planner-p8", "planner", "dead", None)
    rows = list_orphaned_planner_sessions(conn, older_than_minutes=30)
    assert rows == []


def test_worker_single_sweep_kills_nulls_session_and_marks_dead(monkeypatch):
    conn = _db()
    # Missing-plan orphan with an old agent timestamp + a live session.
    _insert_agent(
        conn,
        "planner-px",
        "planner",
        "running",
        "planner-px-sess",
        started_at="2000-01-01",
    )

    # conftest's asyncio.sleep noop is not active here, but we never sleep: the
    # stop_event drives the loop. We let the body run exactly once by setting the
    # stop event from the kill side-effect (so the next loop check exits).
    killed: list[str] = []
    stop = asyncio.Event()

    from murder.runtime.terminal import tmux as tmux_mod

    async def _kill(session: str) -> None:
        killed.append(session)
        stop.set()

    # wait_for(stop.wait(), timeout) must surface a TimeoutError on the first
    # iteration so the sweep body runs; the body sets stop, so the loop's own
    # while-check exits before a second wait_for call.
    async def _wait_for(aw, timeout):  # type: ignore[no-untyped-def]
        aw.close()  # avoid "coroutine never awaited" warning
        raise asyncio.TimeoutError

    monkeypatch.setattr(tmux_mod, "kill_session", _kill)
    monkeypatch.setattr(asyncio, "wait_for", _wait_for)

    worker = PlannerSessionSweeperWorker(sweep_interval_s=0.01)
    ctx = WorkerCtx(repo_root=Path("/tmp"), db=conn)
    asyncio.run(worker.run(ctx, stop))

    assert killed == ["planner-px-sess"]
    row = conn.execute(
        "SELECT session, status FROM agents WHERE agent_id = 'planner-px'"
    ).fetchone()
    assert row["session"] is None
    assert row["status"] == "dead"
