"""Cookbook tests for ticket launch / completion footgun fixes."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.agents.crow_handler import CrowHandler
from murder.completion.coordinator import CompletionCoordinator
from murder.completion.registry import CheckRegistry
from murder.config import CrowHandlerConfig
from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.base import HarnessSession
from murder.harnesses.results import fail_result
from murder.orchestration.orchestrator import Orchestrator
from murder.orchestration.outcome import TicketOutcomeService
from murder.persistence.agents import upsert_agent
from murder.persistence.schema import get_db, init_db
from murder.persistence.tickets import get_ticket_status, insert_ticket
from murder.storage.paths import db_path
from murder.tickets.schema import Ticket
from murder.tickets.status import TicketStatus
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


@pytest.fixture
def artifact_retry_handler(fake_tmux, tmp_path: Path) -> CrowHandler:
    fake_tmux.queue_pane(CC_IDLE + "\n>>> DONE\n")
    runtime = MagicMock()
    runtime.db = MagicMock()
    runtime.bus = MagicMock()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    outcome = MagicMock(spec=TicketOutcomeService)
    coordinator = MagicMock()
    return CrowHandler(
        agent_id="crow_handler-t096",
        ticket_id="t096",
        session="handler-log",
        crow_session="crow-t096",
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=999.0),
        repo_root=tmp_path,
        runtime=runtime,
        outcome=outcome,
        coordinator=coordinator,
    )


def test_artifact_retry_reruns_completion_when_files_appear(
    artifact_retry_handler: CrowHandler,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from murder.completion.coordinator import DoneHandleResult

    repo_root = tmp_path
    conn = _connect(repo_root)
    now = datetime(2026, 5, 28, 12, 0, 0)
    target = repo_root / "murder" / "widget.py"
    target.parent.mkdir(parents=True)
    target.write_text("ok\n", encoding="utf-8")
    insert_ticket(
        conn,
        Ticket(
            id="t096",
            title="artifact race",
            wave=1,
            status=TicketStatus.IN_PROGRESS,
            write_set=[Path("murder/widget.py")],
            created_at=now,
            updated_at=now,
        ),
    )

    artifact_retry_handler.repo_root = repo_root
    artifact_retry_handler.runtime.db = conn
    artifact_retry_handler._artifact_retry_paths = (Path("murder/widget.py"),)
    artifact_retry_handler.coordinator.handle_done = AsyncMock(
        return_value=DoneHandleResult(completed=True)
    )

    monkeypatch.setattr(
        "murder.persistence.tickets.get_ticket_status",
        lambda _db, _tid: TicketStatus.IN_PROGRESS.value,
    )
    monkeypatch.setattr("murder.persistence.agents.heartbeat_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "murder.persistence.tickets.checklist_progress",
        lambda *_a, **_k: (0, 0),
    )
    artifact_retry_handler.runtime.bus.publish = AsyncMock()

    asyncio.run(artifact_retry_handler.tick())

    artifact_retry_handler.coordinator.handle_done.assert_awaited_once_with(
        "t096",
        crow_session="crow-t096",
        start_commit=None,
        repo_root=repo_root,
    )
    assert artifact_retry_handler._artifact_retry_paths is None
