"""Verified harness control construction seam for agent lifecycle."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from murder.state.persistence.connection import RepoDb


class VerifiedControlTarget(Protocol):
    """Minimal agent surface needed to bind verified harness control."""

    id: str
    session: str
    harness: Any
    verified_harness_control: Any
    app_server_connection: Any
    acp_connection: Any
    agent_sdk_connection: Any
    harness_session: Any
    repo_root: Any
    startup_model: str | None
    startup_effort: str | None


@dataclass
class VerifiedControlFactory:
    """Constructs verified harness control for an agent.

    Holds optional prompt-driver overrides so tests can inject policy/sleep
    without monkeypatching ambient Runtime attributes.
    """

    db: RepoDb
    prompt_policy: Any | None = None
    prompt_sleep: Callable[[float], Awaitable[None]] | None = None
    sessions: Any | None = None

    async def initialize(self, agent: VerifiedControlTarget) -> None:
        from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
        from murder.user_config import load_user_config

        options: dict[str, Any] = {}
        if self.prompt_policy is not None:
            options["prompt_policy"] = self.prompt_policy
        if self.prompt_sleep is not None:
            options["prompt_sleep"] = self.prompt_sleep
        tui = load_user_config().tui
        use_app_server = agent.harness.kind == "codex" and tui.codex_control_backend == "app_server"
        use_acp = agent.harness.kind == "cursor" and tui.cursor_control_backend == "acp"
        use_agent_sdk = (
            agent.harness.kind == "claude_code" and tui.claude_control_backend == "agent_sdk"
        )
        if use_app_server:
            connection = getattr(agent, "app_server_connection", None)
            if connection is None:
                from murder.llm.harness_control.app_server.bootstrap import (
                    start_app_server_session,
                )

                harness_session = getattr(agent, "harness_session", None)
                cwd = (
                    harness_session.repo_root
                    if harness_session is not None
                    else getattr(agent, "repo_root", None)
                )
                if cwd is None:
                    raise RuntimeError("app-server bootstrap requires a cwd")
                connection, _client = await start_app_server_session(
                    cwd=cwd,
                    model=getattr(agent, "startup_model", None),
                    model_provider=getattr(agent, "startup_model_provider", None),
                    effort=getattr(agent, "startup_effort", None),
                )
                agent.app_server_connection = connection
            agent.verified_harness_control = VerifiedHarnessControlSession.from_app_server(
                app_server=connection,
                harness_kind=agent.harness.kind,
                terminal_session=agent.session,
                db=self.db,
                persistence_session_id=agent.id,
                **options,
            )
        elif use_acp:
            connection = getattr(agent, "acp_connection", None)
            if connection is None:
                from murder.llm.harness_control.acp.bootstrap import start_acp_session

                harness_session = getattr(agent, "harness_session", None)
                cwd = (
                    harness_session.repo_root
                    if harness_session is not None
                    else getattr(agent, "repo_root", None)
                )
                if cwd is None:
                    raise RuntimeError("ACP bootstrap requires a cwd")
                connection, _client = await start_acp_session(
                    agent="cursor",
                    cwd=cwd,
                    model=getattr(agent, "startup_model", None),
                    effort=getattr(agent, "startup_effort", None),
                )
                agent.acp_connection = connection
            agent.verified_harness_control = VerifiedHarnessControlSession.from_acp(
                acp=connection,
                harness_kind=agent.harness.kind,
                terminal_session=agent.session,
                db=self.db,
                persistence_session_id=agent.id,
                **options,
            )
        elif use_agent_sdk:
            connection = getattr(agent, "agent_sdk_connection", None)
            if connection is None:
                from murder.llm.harness_control.agent_sdk.bootstrap import (
                    start_agent_sdk_session,
                )

                harness_session = getattr(agent, "harness_session", None)
                cwd = (
                    harness_session.repo_root
                    if harness_session is not None
                    else getattr(agent, "repo_root", None)
                )
                if cwd is None:
                    raise RuntimeError("Agent SDK bootstrap requires a cwd")
                connection, _client = await start_agent_sdk_session(
                    cwd=cwd,
                    model=getattr(agent, "startup_model", None),
                    effort=getattr(agent, "startup_effort", None),
                )
                agent.agent_sdk_connection = connection
            agent.verified_harness_control = VerifiedHarnessControlSession.from_agent_sdk(
                agent_sdk=connection,
                harness_kind=agent.harness.kind,
                terminal_session=agent.session,
                db=self.db,
                persistence_session_id=agent.id,
                **options,
            )
        else:
            agent.verified_harness_control = VerifiedHarnessControlSession.from_tmux(
                harness_kind=agent.harness.kind,
                terminal_session=agent.session,
                db=self.db,
                persistence_session_id=agent.id,
                **options,
            )
        await agent.verified_harness_control.ensure_session_controller(
            repository_id=self.db.repository_id,
            agent_key=agent.id,
            sessions=self.sessions,
            recover=True,
        )


__all__ = ["VerifiedControlFactory", "VerifiedControlTarget"]
