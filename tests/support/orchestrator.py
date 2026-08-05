"""Shared Orchestrator construction seam for unit tests (§6.22).

Replaces the dozen per-file ``SimpleNamespace`` / ``rt`` stub factories with one
helper that builds a real ``Orchestrator`` from explicit fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
    RuntimeConfig,
)
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.types import AgentRole, AgentStatus
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.terminal.session_names import SessionNamePolicy


class FakeAgents:
    """In-memory AgentRuntime stand-in for Orchestrator unit tests."""

    def __init__(self) -> None:
        self._by_id: dict[str, Any] = {}
        self._crows: dict[str, Any] = {}
        self._crow_handlers: dict[str, Any] = {}
        self.reaped: list[str] = []
        self.recorded: list[Any] = []
        self.transitions: list[tuple[Any, AgentStatus]] = []
        # CrowAgent.start_conversation probes runtime.db; leave unset for pure
        # in-memory tests unless a caller assigns a real RepoDb.
        self.db: Any | None = None

    def register(self, agent: Any) -> None:
        self._by_id[agent.id] = agent
        ticket_id = getattr(agent, "ticket_id", None)
        role = getattr(agent, "role", None)
        if ticket_id is not None:
            if role == AgentRole.CROW:
                self._crows[ticket_id] = agent
            elif role == AgentRole.CROW_HANDLER:
                self._crow_handlers[ticket_id] = agent

    def record(self, agent: Any) -> None:
        self.recorded.append(agent)
        self._by_id[agent.id] = agent

    async def transition(
        self,
        agent: Any,
        *,
        from_status: AgentStatus | str | None = None,
        to_status: AgentStatus,
        reason: str | None = None,
    ) -> None:
        del from_status, reason
        agent.status = to_status
        self.transitions.append((agent, to_status))
        self.record(agent)

    def find(self, agent_id: str) -> Any | None:
        return self._by_id.get(agent_id)

    def find_crow(self, ticket_id: str) -> Any | None:
        return self._crows.get(ticket_id)

    def find_crow_handler(self, ticket_id: str) -> Any | None:
        return self._crow_handlers.get(ticket_id)

    def all(self) -> tuple[Any, ...]:
        return tuple(self._by_id.values())

    async def reap(self, agent_id: str) -> None:
        self.reaped.append(agent_id)
        agent = self._by_id.pop(agent_id, None)
        if agent is None:
            return
        ticket_id = getattr(agent, "ticket_id", None)
        role = getattr(agent, "role", None)
        if ticket_id is not None:
            if role == AgentRole.CROW:
                self._crows.pop(ticket_id, None)
            elif role == AgentRole.CROW_HANDLER:
                self._crow_handlers.pop(ticket_id, None)

    def rename(self, old_agent_id: str, new_agent_id: str) -> Any:
        agent = self._by_id.pop(old_agent_id)
        agent.id = new_agent_id
        self._by_id[new_agent_id] = agent
        ticket_id = getattr(agent, "ticket_id", None)
        role = getattr(agent, "role", None)
        if ticket_id is not None:
            if role == AgentRole.CROW:
                self._crows[ticket_id] = agent
            elif role == AgentRole.CROW_HANDLER:
                self._crow_handlers[ticket_id] = agent
        return agent

    def emit_lifecycle(self, **_kwargs: Any) -> None:
        return None

    def heartbeat(self, agent_id: str, *, invalidate: bool) -> None:
        del agent_id, invalidate


def default_test_config(*, project_name: str = "demo") -> Config:
    return Config(
        project=ProjectConfig(name=project_name),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
        runtime=RuntimeConfig(session_name_template="murder_{project}_{role}{suffix}"),
    )


def build_test_orchestrator(
    *,
    repo_root: Path | None = None,
    db: Any | None = None,
    config: Config | None = None,
    user_config: Any | None = None,
    agents: FakeAgents | AgentRuntime | None = None,
    run_id: str = "test-run",
    events: Any | None = None,
    commands: Any | None = None,
    event_sink: Any | None = None,
    plan_sync: Any | None = None,
    session_names: SessionNamePolicy | None = None,
) -> Orchestrator:
    """Build a real Orchestrator from fakes — shared seam for unit tests."""
    cfg = config or default_test_config()
    root = repo_root if repo_root is not None else Path("/tmp/murder-test")
    bus = events
    if bus is None:
        bus = MagicMock()
        bus.publish = AsyncMock()
    return Orchestrator(
        config=cfg,
        user_config=user_config,
        repo_root=root,
        db=db if db is not None else MagicMock(),
        run_id=run_id,
        events=bus,
        commands=commands if commands is not None else MagicMock(),
        event_sink=event_sink if event_sink is not None else MagicMock(),
        agents=agents if agents is not None else FakeAgents(),
        session_names=session_names or SessionNamePolicy.from_config(cfg),
        plan_sync=plan_sync,
    )


__all__ = [
    "FakeAgents",
    "build_test_orchestrator",
    "default_test_config",
]
