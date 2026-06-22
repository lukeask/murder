"""Murdering a planner must reap its planning_handler companion (Item 4).

ctrl+m on a ``planner-<plan>`` agent previously left the paired
``planning_handler-<plan>`` orphaned, where it polled the now-dead session and
escalated ("planner missed in poll" red toasts). stop_agent now reaps both.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.runtime.orchestration.agent_ops import AgentOps
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.agents import get_agent_status


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


class _Runtime:
    def __init__(self, db, registered: dict) -> None:
        self.db = db
        self.config = _config()
        self.repo_root = Path("/tmp")
        self._registered = registered
        self.reaped: list[str] = []

    def get_agent(self, agent_id: str):
        return self._registered.get(agent_id)

    async def reap(self, agent_id: str) -> None:
        self.reaped.append(agent_id)


def _ops(rt) -> AgentOps:
    async def _noop_ensure(_plan):  # ensure_planning_agent
        return ""

    async def _noop_collab():
        return ""

    async def _noop_reap_crow(_tid):
        return None

    return AgentOps(
        rt,
        ensure_planning_agent=_noop_ensure,
        ensure_collaborator=_noop_collab,
        reap_ticket_crow_agents=_noop_reap_crow,
        rogue_slug=lambda s: s or "x",
    )


def test_stop_planner_reaps_registered_handler():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    # Both halves are live in the in-memory registry.
    registered = {
        "planner-planX": object(),
        "planning_handler-planX": object(),
    }
    rt = _Runtime(conn, registered)
    ops = _ops(rt)

    result = asyncio.run(ops.stop_agent("planner-planX"))

    assert result["handled"] is True
    # Both the planner and its handler were reaped.
    assert "planner-planX" in rt.reaped
    assert "planning_handler-planX" in rt.reaped


def test_stop_planner_marks_unregistered_handler_dead():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    # Handler exists only in the DB (prior service run), not the registry.
    conn.execute(
        "INSERT INTO agents(agent_id, role, status, session, started_at) "
        "VALUES ('planning_handler-planX', 'planning_handler', 'running', NULL, '2026-01-01')"
    )
    registered = {"planner-planX": object()}  # handler NOT registered
    rt = _Runtime(conn, registered)
    ops = _ops(rt)

    asyncio.run(ops.stop_agent("planner-planX"))

    # The planner was reaped; the orphan DB handler row was marked dead.
    assert "planner-planX" in rt.reaped
    assert get_agent_status(conn, "planning_handler-planX") == "dead"


def test_stop_planner_with_no_handler_is_noop_for_companion():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    registered = {"planner-planX": object()}
    rt = _Runtime(conn, registered)
    ops = _ops(rt)

    # No handler anywhere — must not raise.
    result = asyncio.run(ops.stop_agent("planner-planX"))
    assert result["handled"] is True
    assert rt.reaped == ["planner-planX"]
