"""PlanningHandler self-terminate vs escalate when the planner disappears (Item 4).

Murdering a planner (ctrl+m) orphans its planning_handler, which polls a dead
session. The handler must distinguish a *genuinely gone* planner (no tmux session
AND dead/absent in the DB) — self-terminate quietly — from a *transient* capture
error (session still live) — escalate after the threshold as before.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from murder.config import PlannerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.planning_handler import PlanningHandler
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db


def _db() -> RepoDb:
    return open_test_repo_db(Path(":memory:"))


def _insert_planner(db: RepoDb, plan_name: str, status: str) -> None:
    db.conn.execute(
        "INSERT INTO agents(repository_id, agent_id, role, ticket_id, status, session, started_at) "
        "VALUES (?, ?, 'planner', NULL, ?, ?, '2026-01-01')",
        (db.repository_id, f"planner-{plan_name}", status, f"planner-{plan_name}"),
    )


def _handler(db: RepoDb) -> PlanningHandler:
    runtime = MagicMock()
    runtime.db = db
    runtime.orchestration_events = MagicMock()
    runtime.orchestration_events.publish = AsyncMock()
    runtime.run_id = "test-run"
    runtime.record = MagicMock()
    runtime.transition = AsyncMock()
    return PlanningHandler(
        agent_id="planning_handler-planX",
        session="handler-log",
        planner_session="planner-planX",
        plan_name="planX",
        harness=ClaudeCodeAdapter(),
        config=PlannerConfig(),
        repo_root=Path("/tmp"),
        runtime=runtime,
    )


def test_planner_gone_when_session_absent_and_db_dead(fake_tmux):
    db = _db()
    _insert_planner(db, "planX", "dead")
    handler = _handler(db)
    fake_tmux.set_session_exists(False)  # session gone

    assert asyncio.run(handler._planner_is_gone()) is True


def test_planner_gone_when_session_absent_and_db_row_missing(fake_tmux):
    db = _db()
    # No planner row at all (force-stopped, never reinserted).
    handler = _handler(db)
    fake_tmux.set_session_exists(False)

    assert asyncio.run(handler._planner_is_gone()) is True


def test_planner_not_gone_when_session_still_live(fake_tmux):
    db = _db()
    _insert_planner(db, "planX", "dead")  # DB dead but...
    handler = _handler(db)
    fake_tmux.set_session_exists(True)  # ...session still live → transient blip

    assert asyncio.run(handler._planner_is_gone()) is False


def test_planner_not_gone_when_db_still_running(fake_tmux):
    db = _db()
    _insert_planner(db, "planX", "running")  # alive in DB
    handler = _handler(db)
    fake_tmux.set_session_exists(False)  # session momentarily missing

    # Session absent but DB says running: ambiguous → keep relaying (not gone).
    assert asyncio.run(handler._planner_is_gone()) is False


def test_loop_self_terminates_when_planner_gone(fake_tmux):
    """A poll failure with a genuinely-gone planner stops the handler quietly."""
    db = _db()
    _insert_planner(db, "planX", "dead")
    handler = _handler(db)
    from murder.runtime.agents.base import AgentStatus

    handler.status = AgentStatus.RUNNING
    handler.config.startup_grace_s = 0.0
    fake_tmux.set_session_exists(False)

    # tick() will raise (no pane queued → capture returns empty / harness ok, so
    # force a failing tick explicitly).
    async def _boom() -> None:
        raise RuntimeError("planner pane read failed")

    handler.tick = _boom  # type: ignore[method-assign]
    handler.stop = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(handler._loop())

    # Self-terminated quietly: stop() called, no escalation ErrorEvent published.
    handler.stop.assert_awaited()
    handler.runtime.orchestration_events.publish.assert_not_called()


def test_loop_grace_sleeps_before_first_tick(fake_tmux, monkeypatch):
    """The startup grace window runs before the handler's first capture_pane."""
    import murder.runtime.agents.planning_handler as ph_mod
    from murder.runtime.agents.base import AgentStatus

    db = _db()
    _insert_planner(db, "planX", "running")
    handler = _handler(db)
    handler.status = AgentStatus.RUNNING
    handler.config.startup_grace_s = 2.5
    fake_tmux.set_session_exists(True)

    order: list[str] = []

    async def _record_sleep(secs: float = 0) -> None:
        order.append(f"sleep:{secs}")

    monkeypatch.setattr(ph_mod.asyncio, "sleep", _record_sleep)

    async def _tick_then_stop() -> None:
        order.append("tick")
        handler.status = AgentStatus.DONE

    handler.tick = _tick_then_stop  # type: ignore[method-assign]

    asyncio.run(handler._loop())

    # The grace sleep (2.5s) happened before the first tick.
    assert order[0] == "sleep:2.5"
    assert "tick" in order
    assert order.index("sleep:2.5") < order.index("tick")


def test_loop_escalates_when_planner_still_live(fake_tmux):
    """Transient mid-life misses against a LIVE planner still escalate."""
    from murder.runtime.agents.base import AgentStatus
    from murder.runtime.agents.planning_handler import POLL_FAILURE_ESCALATION_THRESHOLD

    conn = _db()
    _insert_planner(conn, "planX", "running")
    handler = _handler(conn)
    handler.status = AgentStatus.RUNNING
    handler.config.startup_grace_s = 0.0
    fake_tmux.set_session_exists(True)  # planner alive → not gone

    ticks = {"n": 0}

    async def _flaky() -> None:
        ticks["n"] += 1
        if ticks["n"] > POLL_FAILURE_ESCALATION_THRESHOLD:
            # Stop the loop once we've crossed the escalation threshold.
            handler.status = AgentStatus.DONE
            return
        raise RuntimeError("transient pane read")

    handler.tick = _flaky  # type: ignore[method-assign]

    asyncio.run(handler._loop())

    # Escalated exactly once at the threshold (planner is alive, not gone).
    handler.runtime.orchestration_events.publish.assert_awaited_once()
