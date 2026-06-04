from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from murder.runtime.agents.base import AgentRole
from murder.runtime.agents.runner import spawn_agent
from murder.runtime.agents.sessions import AgentScope, AgentSpec


@dataclass
class _Runtime:
    repo_root: Path
    event_sink: object | None = None

    def __post_init__(self) -> None:
        self.agent = None
        self.config = SimpleNamespace(
            project=SimpleNamespace(name="repo"),
            runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        )

    def register_agent(self, agent) -> None:
        self.agent = agent


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
    rt = _Runtime(repo_root=main_root)

    monkeypatch.setattr("murder.runtime.agents.runner.CrowAgent", _FakeCrow)
    monkeypatch.setattr("murder.runtime.agents.runner.get_harness", lambda *_args, **_kw: object())

    spec = AgentSpec(
        role=AgentRole.CROW,
        scope=AgentScope(ticket_id="t001", worktree_path=str(worktree_root)),
        harness="codex",
    )

    asyncio.run(spawn_agent(spec, rt=rt, event_sink=None))

    assert rt.agent is not None
    assert rt.agent.repo_root == worktree_root
    assert rt.agent.worktree_path == worktree_root


def test_spawn_crow_defaults_to_runtime_repo_root(tmp_path: Path, monkeypatch) -> None:
    main_root = tmp_path / "repo"
    rt = _Runtime(repo_root=main_root)

    monkeypatch.setattr("murder.runtime.agents.runner.CrowAgent", _FakeCrow)
    monkeypatch.setattr("murder.runtime.agents.runner.get_harness", lambda *_args, **_kw: object())

    spec = AgentSpec(
        role=AgentRole.CROW,
        scope=AgentScope(ticket_id="t001"),
        harness="codex",
    )

    asyncio.run(spawn_agent(spec, rt=rt, event_sink=None))

    assert rt.agent is not None
    assert rt.agent.repo_root == main_root
    assert rt.agent.worktree_path is None
