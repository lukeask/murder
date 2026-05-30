from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from murder.agents.base import AgentRole
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
    RuntimeConfig,
)
from murder.orchestration.orchestrator import Orchestrator
from murder.persistence.schema import get_db, init_db
from murder.storage.worktrees import WorktreeRef


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


def test_spawn_crow_defaults_to_main_checkout(repo_root: Path, monkeypatch) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 1, 'ready', '2026-01-01', '2026-01-01')
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

    async def fake_ensure(_repo: Path, _ticket_id: str) -> WorktreeRef:
        raise AssertionError("worktrees must be opt-in")

    async def fake_spawn_agent(spec, *, rt, event_sink):
        captured["spec"] = spec
        captured["rt"] = rt
        captured["event_sink"] = event_sink
        return type("Handle", (), {"session_name": "murder_repo_crow_t001"})()

    monkeypatch.setattr("murder.orchestration.orchestrator.ensure_crow_worktree", fake_ensure)
    monkeypatch.setattr("murder.orchestration.orchestrator.spawn_agent", fake_spawn_agent)
    monkeypatch.setattr("murder.orchestration.orchestrator.load", lambda _name: "system")

    session = asyncio.run(Orchestrator(rt).spawn_crow("t001"))  # type: ignore[arg-type]

    assert session == "murder_repo_crow_t001"
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path is None


def test_spawn_crow_provisions_opt_in_worktree_and_puts_it_in_agent_scope(
    repo_root: Path, monkeypatch
) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, wave, status, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 1, 'ready', '2026-01-01', '2026-01-01')
        """
    )
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
        runtime=RuntimeConfig(use_worktrees=True),
    )
    rt = _Runtime(repo_root=repo_root, config=config, db=conn)
    worktree = repo_root / ".murder" / "worktrees" / "crow" / "t001"
    captured = {}

    async def fake_ensure(repo: Path, ticket_id: str) -> WorktreeRef:
        captured["ensure"] = (repo, ticket_id)
        return WorktreeRef(branch="murder/crow/t001", path=worktree)

    async def fake_spawn_agent(spec, *, rt, event_sink):
        captured["spec"] = spec
        captured["rt"] = rt
        captured["event_sink"] = event_sink
        return type("Handle", (), {"session_name": "murder_repo_crow_t001"})()

    monkeypatch.setattr("murder.orchestration.orchestrator.ensure_crow_worktree", fake_ensure)
    monkeypatch.setattr("murder.orchestration.orchestrator.spawn_agent", fake_spawn_agent)
    monkeypatch.setattr("murder.orchestration.orchestrator.load", lambda _name: "system")

    session = asyncio.run(Orchestrator(rt).spawn_crow("t001"))  # type: ignore[arg-type]

    assert session == "murder_repo_crow_t001"
    assert captured["ensure"] == (repo_root, "t001")
    spec = captured["spec"]
    assert spec.role == AgentRole.CROW
    assert spec.scope.ticket_id == "t001"
    assert spec.scope.worktree_path == str(worktree)
