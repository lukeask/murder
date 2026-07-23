from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.runtime.agents.base import AgentRole
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.worktrees import WorktreeRef


@dataclass
class _Runtime:
    repo_root: Path
    config: Config
    db: object
    event_sink: object | None = None
    bus: object | None = None
    run_id: str | None = None

    def get_crow(self, _ticket_id: str):
        return None

    def get_crow_handler(self, _ticket_id: str):
        return None

    def register_agent(self, _agent) -> None:
        return None

    def get_agent(self, _agent_id: str):
        return None

    async def reap(self, _agent_id: str) -> None:
        return None

    def rename_agent(self, _old_agent_id: str, _new_agent_id: str, *, persist=None):
        return None


class _LiveHarness:
    kind = "codex"


class _LiveCollaborator:
    harness = _LiveHarness()

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        self.stopped = True


def test_reconfigure_collaborator_restarts_when_saved_harness_changes(
    repo_root: Path, monkeypatch
) -> None:
    # Isolate user config: collaborator harness now resolves from user scope /
    # bundled defaults (claude_code), never the project roles.yaml.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(repo_root.parent / "xdg"))
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    roles_path = repo_root / ".murder" / "roles.yaml"
    roles_path.write_text(
        Path("murder/resources/templates/roles.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    live = _LiveCollaborator()
    reaped: list[str] = []
    ensured: list[bool] = []

    class _RuntimeWithCollaborator(_Runtime):
        def get_agent(self, agent_id: str):
            return live if agent_id == "collaborator-0" else None

        async def reap(self, agent_id: str) -> None:
            reaped.append(agent_id)

    rt = _RuntimeWithCollaborator(repo_root=repo_root, config=config, db=conn)
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
    orch = Orchestrator(rt)  # type: ignore[arg-type]

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
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    roles_path = repo_root / ".murder" / "roles.yaml"
    roles_path.write_text(
        Path("murder/resources/templates/roles.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    live = _LiveCollaborator()

    class _RuntimeWithCollaborator(_Runtime):
        def get_agent(self, agent_id: str):
            return live if agent_id == "collaborator-0" else None

    rt = _RuntimeWithCollaborator(repo_root=repo_root, config=config, db=conn)
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
    orch = Orchestrator(rt)  # type: ignore[arg-type]

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
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, status, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 'ready', '2026-01-01', '2026-01-01')
        """
    )
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt = _Runtime(repo_root=repo_root, config=config, db=conn)
    captured = {}

    async def fake_ensure(_repo: Path, _branch_name: str, **_kwargs: object) -> WorktreeRef:
        raise AssertionError("worktrees must be opt-in")

    async def fake_spawn_agent(spec, *, rt, event_sink):
        captured["spec"] = spec
        captured["rt"] = rt
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

    session = asyncio.run(Orchestrator(rt).spawn_crow("t001"))  # type: ignore[arg-type]

    assert session == "murder_repo_crow_t001"
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path is None
    assert spec.additional_workspace_dirs == ()


def test_spawn_crow_provisions_opt_in_worktree_and_puts_it_in_agent_scope(
    repo_root: Path, monkeypatch
) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, status, worktree, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 'ready', 'feature/c6', '2026-01-01', '2026-01-01')
        """
    )
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt = _Runtime(repo_root=repo_root, config=config, db=conn)
    worktree = repo_root / ".murder" / "worktrees" / "feature-c6"
    captured = {}

    async def fake_ensure(repo: Path, branch_name: str, **_kwargs: object) -> WorktreeRef:
        captured["ensure"] = (repo, branch_name)
        return WorktreeRef(branch="feature/c6", path=worktree)

    async def fake_spawn_agent(spec, *, rt, event_sink):
        captured["spec"] = spec
        captured["rt"] = rt
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

    session = asyncio.run(Orchestrator(rt).spawn_crow("t001"))  # type: ignore[arg-type]

    assert session == "murder_repo_crow_t001"
    assert captured["ensure"] == (repo_root, "feature/c6")
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path == str(worktree)
    assert spec.additional_workspace_dirs == (
        str((repo_root / ".murder" / "tickets").resolve()),
    )
