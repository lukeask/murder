"""Cookbook tests for ticket launch / completion footgun fixes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.agents.crow_handler import CrowHandler
from murder.verdict.completion.coordinator import CompletionCoordinator
from murder.verdict.completion.registry import CheckRegistry
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.base import HarnessSession
from murder.llm.harnesses.results import fail_result
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.outcome import TicketOutcomeService
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import get_ticket_status, insert_ticket
from murder.state.storage.paths import db_path
from murder.work.tickets.schema import Ticket
from murder.work.tickets.status import TicketStatus
from tests.unit.test_harness_adapters import CC_IDLE


def _connect(repo_root: Path):
    conn = get_db(db_path(repo_root))
    init_db(conn)
    return conn


def test_kickoff_reaps_stale_running_agents_when_ticket_still_ready(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _connect(repo_root)
    now = datetime(2026, 5, 28, 12, 0, 0)
    insert_ticket(
        conn,
        Ticket(
            id="t099",
            title="stale crow blocks kickoff",
            wave=1,
            status=TicketStatus.READY,
            created_at=now,
            updated_at=now,
        ),
    )
    upsert_agent(
        conn,
        agent_id="crow-t099",
        role="crow",
        ticket_id="t099",
        session="murder_demo_crow_t099",
        status="running",
    )
    reaped: list[str] = []

    async def _reap(agent_id: str) -> None:
        reaped.append(agent_id)

    rt = SimpleNamespace(
        db=conn,
        bus=MagicMock(),
        run_id="test-run",
        repo_root=repo_root,
        reap=_reap,
        get_crow=lambda _tid: None,
        sync_agent=MagicMock(),
    )
    orch = Orchestrator(rt)
    monkeypatch.setattr(orch, "spawn_crow", AsyncMock(return_value="crow-sess"))
    monkeypatch.setattr(orch, "spawn_crow_handler", AsyncMock())
    monkeypatch.setattr(
        orch,
        "_emit_ticket_status",
        AsyncMock(),
    )
    fake_crow = SimpleNamespace(session="crow-sess")
    rt.get_crow = lambda _tid: fake_crow  # type: ignore[method-assign]

    kicked = asyncio.run(orch.kickoff_ready(only="t099"))

    assert kicked == ["t099"]
    assert reaped == ["crow-t099", "crow_handler-t099"]
    assert get_ticket_status(conn, "t099") == TicketStatus.IN_PROGRESS.value


def test_force_ticket_status_reaps_crow_agents(repo_root: Path) -> None:
    conn = _connect(repo_root)
    now = datetime(2026, 5, 28, 12, 0, 0)
    insert_ticket(
        conn,
        Ticket(
            id="t098",
            title="force done",
            wave=1,
            status=TicketStatus.IN_PROGRESS,
            created_at=now,
            updated_at=now,
        ),
    )
    reaped: list[str] = []

    async def _reap(agent_id: str) -> None:
        reaped.append(agent_id)

    bus = MagicMock()
    bus.publish = AsyncMock()
    rt = SimpleNamespace(
        db=conn,
        bus=bus,
        run_id="test-run",
        repo_root=repo_root,
        reap=_reap,
    )
    orch = Orchestrator(rt)

    result = asyncio.run(orch.force_ticket_status("t098", "done"))

    assert result["ok"] is True
    assert reaped == ["crow-t098", "crow_handler-t098"]
    assert get_ticket_status(conn, "t098") == TicketStatus.DONE.value


def test_set_schedule_at_updates_ticket_timestamp(repo_root: Path) -> None:
    conn = _connect(repo_root)
    created = datetime(2026, 5, 28, 12, 0, 0)
    insert_ticket(
        conn,
        Ticket(
            id="t097a",
            title="schedule me",
            wave=1,
            status=TicketStatus.PLANNED,
            created_at=created,
            updated_at=created,
        ),
    )
    rt = SimpleNamespace(db=conn, repo_root=repo_root, bus=None, run_id=None)
    orch = Orchestrator(rt)

    asyncio.run(orch.set_schedule_at("t097a", "2026-05-29T09:00:00"))

    row = conn.execute(
        "SELECT schedule_at, updated_at FROM tickets WHERE id = ?", ("t097a",)
    ).fetchone()
    assert row["schedule_at"] == "2026-05-29T09:00:00"
    assert row["updated_at"] != created.isoformat(timespec="seconds")


def test_codex_rogue_keeps_startup_model_session_on_runtime_picker_failure(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: dict[str, object] = {}
    reaped: list[str] = []

    async def _reap(agent_id: str) -> None:
        reaped.append(agent_id)

    async def _fail_model_selection(self: HarnessSession, spec) -> object:
        return fail_result(
            "codex failed to select runtime model 'gpt-5.4-mini' with effort 'medium'"
        )

    monkeypatch.setattr(HarnessSession, "start", _fail_model_selection)

    rt = SimpleNamespace(
        db=MagicMock(),
        bus=MagicMock(),
        run_id="test-run",
        repo_root=repo_root,
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        ),
        get_agent=lambda agent_id: agents.get(agent_id),
        register_agent=lambda agent: agents.setdefault(agent.id, agent),
        sync_agent=MagicMock(),
        reap=_reap,
    )
    orch = Orchestrator(rt)

    agent_id = asyncio.run(orch.spawn_rogue("codex", "gpt-5.4-mini"))

    assert agent_id in agents
    assert reaped == []
    agent = agents[agent_id]
    assert agent.harness_session._first_send_idle_gate_pending is True  # noqa: SLF001
    rt.sync_agent.assert_called_once()


def test_codex_rogue_keeps_startup_model_session_on_idle_timeout(
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents: dict[str, object] = {}
    reaped: list[str] = []

    async def _reap(agent_id: str) -> None:
        reaped.append(agent_id)

    async def _fail_idle_wait(self: HarnessSession, spec) -> object:
        return fail_result("Harness not idle in time: session=murder_test_crow_codex_rogue_test")

    def _get_agent(agent_id: str) -> object | None:
        return agents.get(agent_id)

    monkeypatch.setattr(HarnessSession, "start", _fail_idle_wait)

    rt = SimpleNamespace(
        db=MagicMock(),
        bus=MagicMock(),
        run_id="test-run",
        repo_root=repo_root,
        config=SimpleNamespace(
            project=SimpleNamespace(name="test"),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        ),
        get_agent=_get_agent,
        register_agent=lambda agent: agents.setdefault(agent.id, agent),
        sync_agent=MagicMock(),
        reap=_reap,
    )
    orch = Orchestrator(rt)

    agent_id = asyncio.run(orch.spawn_rogue("codex", "gpt-5.4-mini"))

    assert agent_id in agents
    assert reaped == []
    agent = agents[agent_id]
    assert agent.harness_session._first_send_idle_gate_pending is True  # noqa: SLF001
    rt.sync_agent.assert_called_once()


def test_transition_done_heals_ready_status(repo_root: Path) -> None:
    conn = _connect(repo_root)
    now = datetime(2026, 5, 28, 12, 0, 0)
    insert_ticket(
        conn,
        Ticket(
            id="t097",
            title="ready but crow finished",
            wave=1,
            status=TicketStatus.READY,
            created_at=now,
            updated_at=now,
        ),
    )
    rt = SimpleNamespace(db=conn, repo_root=repo_root, bus=None, run_id=None)
    coordinator = CompletionCoordinator(rt, CheckRegistry())

    asyncio.run(
        coordinator._transition_done("t097")  # noqa: SLF001 — unit test of safety net
    )

    assert get_ticket_status(conn, "t097") == TicketStatus.DONE.value
