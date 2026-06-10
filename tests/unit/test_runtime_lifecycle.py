from __future__ import annotations

import asyncio

from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig, RuntimeConfig
from murder.app.service.runtime_lifecycle import kill_project_tmux_sessions


def test_kill_project_tmux_sessions_only_kills_project_prefix(monkeypatch) -> None:
    killed: list[str] = []

    async def _list_sessions(prefix: str | None = None) -> list[str]:
        sessions = [
            "murder_repo_crow_t1",
            "murder_repo_usage_codex",
            "murder_other_crow_t2",
        ]
        return [name for name in sessions if prefix is None or name.startswith(prefix)]

    async def _kill_session(name: str) -> None:
        killed.append(name)

    monkeypatch.setattr("murder.app.service.runtime_lifecycle.tmux.list_sessions", _list_sessions)
    monkeypatch.setattr("murder.app.service.runtime_lifecycle.tmux.kill_session", _kill_session)

    role = HarnessRoleConfig(harness="codex")
    scope = type(
        "Scope",
        (),
        {
            "config": Config(
                project=ProjectConfig(name="repo"),
                runtime=RuntimeConfig(session_name_template="murder_{project}_{role}{suffix}"),
                collaborator=role,
                default_crow=role,
                crow_handler=CrowHandlerConfig(model="test-model"),
            )
        },
    )()

    sessions = asyncio.run(kill_project_tmux_sessions(scope))

    assert sessions == ["murder_repo_crow_t1", "murder_repo_usage_codex"]
    assert killed == sessions
