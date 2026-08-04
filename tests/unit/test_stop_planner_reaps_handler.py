"""Murdering a planner must reap its planning_handler companion (Item 4).

ctrl+m on a ``planner-<plan>`` agent previously left the paired
``planning_handler-<plan>`` orphaned, where it polled the now-dead session and
escalated ("planner missed in poll" red toasts). stop_agent now reaps both.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from murder.runtime.orchestration.agent_ops import AgentOps
from murder.state.persistence.agents import get_agent_status
from murder.state.persistence.connection import RepoDb


class _Agents:
    """Minimal AgentRuntime stand-in for stop_agent companion-reap tests."""

    def __init__(self, registered: dict) -> None:
        self._registered = registered
        self.reaped: list[str] = []

    def find(self, agent_id: str):
        return self._registered.get(agent_id)

    async def reap(self, agent_id: str) -> None:
        self.reaped.append(agent_id)


def _ops(db: RepoDb, agents: _Agents) -> AgentOps:
    async def _noop_ensure(_plan):  # ensure_planning_agent
        return ""

    async def _noop_collab():
        return ""

    async def _noop_reap_crow(_tid):
        return None

    return AgentOps(
        db=db,
        agents=agents,
        events=MagicMock(),
        run_id="test-run",
        ensure_planning_agent=_noop_ensure,
        ensure_collaborator=_noop_collab,
        reap_ticket_crow_agents=_noop_reap_crow,
        rogue_slug=lambda s: s or "x",
    )


def _db() -> RepoDb:
    from tests.support.database import open_test_repo_db

    return open_test_repo_db(Path(":memory:"))


def test_stop_planner_reaps_registered_handler():
    db = _db()
    # Both halves are live in the in-memory registry.
    registered = {
        "planner-planX": object(),
        "planning_handler-planX": object(),
    }
    agents = _Agents(registered)
    ops = _ops(db, agents)

    result = asyncio.run(ops.stop_agent("planner-planX"))

    assert result["handled"] is True
    # Both the planner and its handler were reaped.
    assert "planner-planX" in agents.reaped
    assert "planning_handler-planX" in agents.reaped


def test_stop_planner_marks_unregistered_handler_dead():
    db = _db()
    # Handler exists only in the DB (prior service run), not the registry.
    db.conn.execute(
        "INSERT INTO agents(repository_id, agent_id, role, status, session, started_at) "
        "VALUES (?, 'planning_handler-planX', 'planning_handler', 'running', NULL, '2026-01-01')",
        (db.repository_id,),
    )
    registered = {"planner-planX": object()}  # handler NOT registered
    agents = _Agents(registered)
    ops = _ops(db, agents)

    asyncio.run(ops.stop_agent("planner-planX"))

    # The planner was reaped; the orphan DB handler row was marked dead.
    assert "planner-planX" in agents.reaped
    assert get_agent_status(db, "planning_handler-planX") == "dead"


def test_stop_planner_with_no_handler_is_noop_for_companion():
    db = _db()
    registered = {"planner-planX": object()}
    agents = _Agents(registered)
    ops = _ops(db, agents)

    # No handler anywhere — must not raise.
    result = asyncio.run(ops.stop_agent("planner-planX"))
    assert result["handled"] is True
    assert agents.reaped == ["planner-planX"]
