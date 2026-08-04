"""Unit tests for AgentRuntime (§6.4 / §9)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.observability.advanced_log import NullAdvancedLog
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.types import AgentRole, AgentStatus
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.events import AgentLifecycleEvent, StatusChangeEvent
from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.roster.service import RosterService
from murder.state.storage.paths import db_path
from tests.support.database import open_test_repo_db


class _RecordingAgent:
    def __init__(
        self,
        agent_id: str,
        *,
        role: AgentRole = AgentRole.CROW,
        ticket_id: str | None = "t1",
        status: AgentStatus = AgentStatus.RUNNING,
    ) -> None:
        self.id = agent_id
        self.role = role
        self.ticket_id = ticket_id
        self.session = f"sess-{agent_id}"
        self.harness = SimpleNamespace(kind="codex")
        self.startup_model = "test-model"
        self.status = status
        self.start_commit = None
        self.worktree_path = None
        self.stop_calls: list[dict[str, object]] = []

    async def stop(self, *, failed: bool = True, kill_session: bool = True) -> None:
        self.stop_calls.append({"failed": failed, "kill_session": kill_session})


def _connect(repo_root):
    database = db_path(repo_root)
    database.parent.mkdir(parents=True, exist_ok=True)
    return open_test_repo_db(database)


def _insert_ticket(db, ticket_id: str) -> None:
    db.conn.execute(
        "INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'in_progress', '2026-01-01', '2026-01-01')",
        (db.repository_id, ticket_id, f"Title {ticket_id}"),
    )


def _make_runtime(db, *, preserve: bool = False, events=None) -> AgentRuntime:
    bus = events or InProcessOrchestrationEventSink()
    return AgentRuntime(
        db=db,
        roster=RosterService(db),
        events=bus,
        run_id="run-test",
        advanced_log=NullAdvancedLog(),
        preserve_tmux_on_close=lambda: preserve,
        verified_control_factory=VerifiedControlFactory(db=db),
        lifecycle_events_enabled=True,
    )


def test_register_is_atomic_with_persistence(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    agent = _RecordingAgent("crow-t1")

    agents.register(agent)

    assert agents.find("crow-t1") is agent
    assert agents.find_crow("t1") is agent
    row = db.conn.execute(
        "SELECT agent_id, status FROM agents WHERE agent_id = ?", ("crow-t1",)
    ).fetchone()
    assert row is not None
    assert row["status"] == "running"


def test_register_rolls_back_index_on_persist_failure(repo_root, monkeypatch) -> None:
    db = _connect(repo_root)
    agents = _make_runtime(db)
    agent = _RecordingAgent("crow-t1")

    monkeypatch.setattr(
        agents,
        "record",
        lambda _a: (_ for _ in ()).throw(RuntimeError("persist boom")),
    )
    with pytest.raises(RuntimeError, match="persist boom"):
        agents.register(agent)
    assert agents.find("crow-t1") is None


def test_register_duplicate_id_fails(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    agents.register(_RecordingAgent("crow-t1"))
    with pytest.raises(ValueError, match="already registered"):
        agents.register(_RecordingAgent("crow-t1"))


def test_rename_rolls_back_on_persistence_failure(repo_root, monkeypatch) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    agent = _RecordingAgent("a-old", role=AgentRole.COLLABORATOR, ticket_id=None)
    agents.register(agent)

    real_record = agents.record

    def _flaky(a) -> None:
        raise RuntimeError("rename persist boom")

    monkeypatch.setattr(agents, "record", _flaky)
    with pytest.raises(RuntimeError, match="rename persist boom"):
        agents.rename("a-old", "a-new")
    assert agents.find("a-old") is agent
    assert agents.find("a-new") is None
    assert agent.id == "a-old"


def test_reap_crow_preserves_handler_index(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    crow = _RecordingAgent("crow-t1", role=AgentRole.CROW, ticket_id="t1")
    handler = _RecordingAgent(
        "crow_handler-t1", role=AgentRole.CROW_HANDLER, ticket_id="t1"
    )
    agents.register(crow)
    agents.register(handler)

    asyncio.run(agents.reap("crow-t1"))

    assert agents.find("crow-t1") is None
    assert agents.find_crow("t1") is None
    assert agents.find_crow_handler("t1") is handler


def test_reap_handler_preserves_crow_index(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    crow = _RecordingAgent("crow-t1", role=AgentRole.CROW, ticket_id="t1")
    handler = _RecordingAgent(
        "crow_handler-t1", role=AgentRole.CROW_HANDLER, ticket_id="t1"
    )
    agents.register(crow)
    agents.register(handler)

    asyncio.run(agents.reap("crow_handler-t1"))

    assert agents.find_crow_handler("t1") is None
    assert agents.find_crow("t1") is crow


def test_transition_persists_and_publishes(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    bus = InProcessOrchestrationEventSink()
    published: list[object] = []

    async def _capture(event: object) -> None:
        published.append(event)

    bus.subscribe(_capture)
    agents = _make_runtime(db, events=bus)
    agent = _RecordingAgent("crow-t1", status=AgentStatus.IDLE)
    agents.register(agent)

    async def _drive() -> None:
        await agents.transition(
            agent,
            from_status=AgentStatus.IDLE,
            to_status=AgentStatus.RUNNING,
        )

    asyncio.run(_drive())
    assert agent.status is AgentStatus.RUNNING
    assert any(isinstance(e, StatusChangeEvent) for e in published)


def test_close_stops_agents_concurrently_and_respects_tmux_policy(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db, preserve=True)
    a1 = _RecordingAgent("crow-t1")
    a2 = _RecordingAgent("collab", role=AgentRole.COLLABORATOR, ticket_id=None)
    agents.register(a1)
    agents.register(a2)

    asyncio.run(agents.close())

    assert a1.stop_calls == [{"failed": True, "kill_session": False}]
    assert a2.stop_calls == [{"failed": True, "kill_session": False}]
    assert agents.all() == ()


def test_authoritative_close_kills_owned_sessions(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db, preserve=False)
    agent = _RecordingAgent("crow-t1", status=AgentStatus.RUNNING)
    agents.register(agent)

    asyncio.run(agents.close())

    assert agent.stop_calls == [{"failed": True, "kill_session": True}]


def test_close_does_not_mark_terminal_agents_failed(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db, preserve=False)
    agent = _RecordingAgent("crow-t1", status=AgentStatus.DONE)
    agents.register(agent)

    asyncio.run(agents.close())

    assert agent.stop_calls == [{"failed": False, "kill_session": True}]


def test_lifecycle_events_stop_after_close(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    bus = InProcessOrchestrationEventSink()
    published: list[object] = []

    async def _capture(event: object) -> None:
        published.append(event)

    bus.subscribe(_capture)
    agents = _make_runtime(db, events=bus)
    agent = _RecordingAgent("crow-t1")
    agents.register(agent)

    async def _drive() -> None:
        await asyncio.sleep(0)  # let register lifecycle task schedule
        await agents.close()
        before = len(published)
        agents.emit_lifecycle(op="force_stop", agent_id="x")
        await asyncio.sleep(0)
        assert len(published) == before

    asyncio.run(_drive())


def test_lifecycle_emission_tasks_are_drained_on_close(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    bus = InProcessOrchestrationEventSink()
    published: list[object] = []

    async def _capture(event: object) -> None:
        published.append(event)

    bus.subscribe(_capture)
    agents = _make_runtime(db, events=bus)

    async def _drive() -> None:
        # Register must run with a live loop so lifecycle emission is scheduled.
        agents.register(_RecordingAgent("crow-t1"))
        pending = list(agents._emit_tasks)  # noqa: SLF001
        assert pending
        await asyncio.gather(*pending, return_exceptions=True)
        assert any(isinstance(e, AgentLifecycleEvent) for e in published)
        await agents.close()
        assert agents._emit_tasks == set()  # noqa: SLF001

    asyncio.run(_drive())


def test_role_indexes_track_crow_and_handler(repo_root) -> None:
    db = _connect(repo_root)
    _insert_ticket(db, "t1")
    agents = _make_runtime(db)
    crow = _RecordingAgent("crow-t1", role=AgentRole.CROW, ticket_id="t1")
    handler = _RecordingAgent(
        "crow_handler-t1", role=AgentRole.CROW_HANDLER, ticket_id="t1"
    )
    agents.register(crow)
    agents.register(handler)
    assert agents.find_crow("t1") is crow
    assert agents.find_crow_handler("t1") is handler
    assert set(a.id for a in agents.all()) == {"crow-t1", "crow_handler-t1"}


def test_rename_persists_identity(repo_root) -> None:
    db = _connect(repo_root)
    agents = _make_runtime(db)
    agent = _RecordingAgent("planner-old", role=AgentRole.PLANNER, ticket_id=None)
    agents.register(agent)
    renamed = agents.rename("planner-old", "planner-new")
    assert renamed is agent
    assert agent.id == "planner-new"
    assert agents.find("planner-old") is None
    row = db.conn.execute(
        "SELECT agent_id FROM agents WHERE agent_id = ?", ("planner-new",)
    ).fetchone()
    assert row is not None
