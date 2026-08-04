from __future__ import annotations

import asyncio
from pathlib import Path

from murder.runtime.agents.base import AgentRole
from murder.runtime.agents.runner import spawn_agent
from murder.runtime.agents.sessions import AgentScope, AgentSpec
from murder.runtime.terminal.session_names import SessionNamePolicy
from tests.support.orchestrator import FakeAgents


class _FakeCrow:
    role = AgentRole.CROW

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)
        self.id = kwargs["agent_id"]
        self.session = kwargs["session"]

    async def start(self, _prompt: str, _ctx: dict) -> None:
        return None


def test_spawn_crow_uses_scope_worktree_as_repo_root(tmp_path: Path, monkeypatch) -> None:
    main_root = tmp_path / "repo"
    worktree_root = tmp_path / "repo" / ".murder" / "worktrees" / "crow" / "t001"
    agents = FakeAgents()
    names = SessionNamePolicy(project_name="repo", template="murder_{project}_{role}{suffix}")

    monkeypatch.setattr("murder.runtime.agents.runner.CrowAgent", _FakeCrow)
    monkeypatch.setattr("murder.runtime.agents.runner.get_harness", lambda *_args, **_kw: object())

    spec = AgentSpec(
        role=AgentRole.CROW,
        scope=AgentScope(ticket_id="t001", worktree_path=str(worktree_root)),
        harness="codex",
    )

    asyncio.run(
        spawn_agent(
            spec,
            repo_root=main_root,
            session_names=names,
            agents=agents,  # type: ignore[arg-type]
            event_sink=None,
        )
    )

    agent = agents.find("crow-t001")
    assert agent is not None
    assert agent.repo_root == worktree_root
    assert agent.worktree_path == worktree_root


def test_spawn_crow_defaults_to_runtime_repo_root(tmp_path: Path, monkeypatch) -> None:
    main_root = tmp_path / "repo"
    agents = FakeAgents()
    names = SessionNamePolicy(project_name="repo", template="murder_{project}_{role}{suffix}")

    monkeypatch.setattr("murder.runtime.agents.runner.CrowAgent", _FakeCrow)
    monkeypatch.setattr("murder.runtime.agents.runner.get_harness", lambda *_args, **_kw: object())

    spec = AgentSpec(
        role=AgentRole.CROW,
        scope=AgentScope(ticket_id="t001"),
        harness="codex",
    )

    asyncio.run(
        spawn_agent(
            spec,
            repo_root=main_root,
            session_names=names,
            agents=agents,  # type: ignore[arg-type]
            event_sink=None,
        )
    )

    agent = agents.find("crow-t001")
    assert agent is not None
    assert agent.repo_root == main_root
    assert agent.worktree_path is None
