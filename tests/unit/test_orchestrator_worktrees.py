from __future__ import annotations

import asyncio
from pathlib import Path

from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.runtime.agents.types import AgentRole
from murder.state.persistence.agents import upsert_agent
from murder.state.storage.worktrees import WorktreeRef
from tests.support.database import open_test_repo_db
from tests.support.orchestrator import FakeAgents, build_test_orchestrator


class _LiveHarness:
    kind = "codex"


class _LiveCollaborator:
    harness = _LiveHarness()

    def __init__(self, agent_id: str = "collaborator-0") -> None:
        self.id = agent_id
        self.stopped = False

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del failed, kill_session
        self.stopped = True


async def _skip_model_refresh(*_args, **_kwargs) -> None:
    return None


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def test_reconfigure_collaborator_restarts_when_saved_harness_changes(
    repo_root: Path, monkeypatch
) -> None:
    # Isolate user config: collaborator harness now resolves from user scope /
    # bundled defaults (claude_code), never the project roles.yaml.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(repo_root.parent / "xdg"))
    monkeypatch.setattr(
        "murder.llm.harnesses.model_cache.refresh_and_persist_harness_models",
        _skip_model_refresh,
    )
    db_path = repo_root / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_test_repo_db(db_path)
    roles_path = repo_root / ".murder" / "roles.yaml"
    roles_path.write_text(
        Path("murder/resources/templates/roles.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    live = _LiveCollaborator()
    agents = FakeAgents()
    agents.register(live)
    reaped: list[str] = []
    ensured: list[bool] = []

    async def _tracking_reap(agent_id: str) -> None:
        reaped.append(agent_id)
        await FakeAgents.reap(agents, agent_id)

    agents.reap = _tracking_reap  # type: ignore[method-assign]
    upsert_agent(
        conn,
        agent_id="collaborator-0",
        role="collaborator",
        ticket_id=None,
        session="murder_repo_collaborator",
        harness="codex",
        model=None,
        status="running",
        start_commit=None,
        worktree_path=None,
        pid=None,
    )
    orch = build_test_orchestrator(
        repo_root=repo_root, config=_config(), db=conn, agents=agents
    )

    async def _ensure() -> str:
        ensured.append(True)
        return "collaborator-0"

    monkeypatch.setattr(orch, "ensure_collaborator", _ensure)

    result = asyncio.run(orch.reconfigure_collaborator())

    assert result["changed"] is True
    assert result["previous_harness"] == "codex"
    assert result["harness"] == "claude_code"
    assert live.stopped is True
    assert reaped == ["collaborator-0"]
    assert ensured == [True]


def test_reconfigure_collaborator_returns_startup_failure_error(
    repo_root: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(repo_root.parent / "xdg"))
    monkeypatch.setattr(
        "murder.llm.harnesses.model_cache.refresh_and_persist_harness_models",
        _skip_model_refresh,
    )
    db_path = repo_root / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_test_repo_db(db_path)
    roles_path = repo_root / ".murder" / "roles.yaml"
    roles_path.write_text(
        Path("murder/resources/templates/roles.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    live = _LiveCollaborator()
    agents = FakeAgents()
    agents.register(live)
    upsert_agent(
        conn,
        agent_id="collaborator-0",
        role="collaborator",
        ticket_id=None,
        session="murder_repo_collaborator",
        harness="codex",
        model=None,
        status="running",
        start_commit=None,
        worktree_path=None,
        pid=None,
    )
    orch = build_test_orchestrator(
        repo_root=repo_root, config=_config(), db=conn, agents=agents
    )

    async def _ensure() -> str:
        raise TimeoutError("Harness not awaiting input in time: session=collaborator-0")

    monkeypatch.setattr(orch, "ensure_collaborator", _ensure)

    result = asyncio.run(orch.reconfigure_collaborator())

    assert result["ok"] is False
    assert result["changed"] is True
    assert result["error"] == "Harness not awaiting input in time: session=collaborator-0"
    assert result["restarted"] is False
    assert live.stopped is True


def test_spawn_crow_defaults_to_main_checkout(repo_root: Path, monkeypatch) -> None:
    db_path = repo_root / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_test_repo_db(db_path)
    conn.conn.execute(
        """
        INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at)
        VALUES (?, 't001', 'Fix thing', 'ready', '2026-01-01', '2026-01-01')
        """,
        (conn.repository_id,),
    )
    orch = build_test_orchestrator(repo_root=repo_root, config=_config(), db=conn)
    captured = {}

    async def fake_ensure(_repo: Path, _branch_name: str, **_kwargs: object) -> WorktreeRef:
        raise AssertionError("worktrees must be opt-in")

    async def fake_spawn_agent(spec, *, repo_root, session_names, agents, event_sink=None):
        captured["spec"] = spec
        captured["event_sink"] = event_sink
        return type("Handle", (), {"session_name": "murder_repo_crow_t001"})()

    class _FakeAssembler:
        def build(self, _ctx) -> str:
            return "brief"

    monkeypatch.setattr(
        "murder.runtime.orchestration.worktree_provisioner.ensure_worktree_for_branch",
        fake_ensure,
    )
    monkeypatch.setattr(
        "murder.runtime.orchestration.orchestrator.spawn_agent",
        fake_spawn_agent,
    )
    monkeypatch.setattr(
        "murder.runtime.orchestration.brief_service.assembler_for",
        lambda _ctx: _FakeAssembler(),
    )

    session = asyncio.run(orch.spawn_crow("t001"))

    assert session == "murder_repo_crow_t001"
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path is None
    assert spec.additional_workspace_dirs == ()


def test_spawn_crow_provisions_opt_in_worktree_and_puts_it_in_agent_scope(
    repo_root: Path, monkeypatch
) -> None:
    db_path = repo_root / ".murder" / "murder.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_test_repo_db(db_path)
    conn.conn.execute(
        """
        INSERT INTO tickets(repository_id, id, title, status, worktree, created_at, updated_at)
        VALUES (?, 't001', 'Fix thing', 'ready', 'feature/c6', '2026-01-01', '2026-01-01')
        """,
        (conn.repository_id,),
    )
    orch = build_test_orchestrator(repo_root=repo_root, config=_config(), db=conn)
    worktree = repo_root / ".murder" / "worktrees" / "feature-c6"
    captured = {}

    async def fake_ensure(repo: Path, branch_name: str, **_kwargs: object) -> WorktreeRef:
        captured["ensure"] = (repo, branch_name)
        return WorktreeRef(branch="feature/c6", path=worktree)

    async def fake_spawn_agent(spec, *, repo_root, session_names, agents, event_sink=None):
        captured["spec"] = spec
        captured["event_sink"] = event_sink
        return type("Handle", (), {"session_name": "murder_repo_crow_t001"})()

    class _FakeAssembler:
        def build(self, _ctx) -> str:
            return "brief"

    monkeypatch.setattr(
        "murder.runtime.orchestration.worktree_provisioner.ensure_worktree_for_branch",
        fake_ensure,
    )
    monkeypatch.setattr(
        "murder.runtime.orchestration.orchestrator.spawn_agent",
        fake_spawn_agent,
    )
    monkeypatch.setattr(
        "murder.runtime.orchestration.brief_service.assembler_for",
        lambda _ctx: _FakeAssembler(),
    )

    session = asyncio.run(orch.spawn_crow("t001"))

    assert session == "murder_repo_crow_t001"
    assert captured["ensure"] == (repo_root, "feature/c6")
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path == str(worktree)
    assert spec.additional_workspace_dirs == (
        str((repo_root / ".murder" / "tickets").resolve()),
    )
