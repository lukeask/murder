"""Shared Orchestrator construction seam for unit tests (§6.22).

Replaces the dozen per-file ``SimpleNamespace`` / ``rt`` stub factories with one
helper that builds a real ``Orchestrator`` from explicit fakes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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


class _DelegatingAgents:
    """AgentRuntime-shaped facade that always reads methods from a live rt stub."""

    def __init__(self, rt: Any) -> None:
        self._rt = rt
        self._fallback = FakeAgents()
        self.reaped = self._fallback.reaped
        self.transitions = self._fallback.transitions

    @property
    def db(self) -> Any:
        return getattr(self._rt, "db", None)

    @property
    def orchestration_events(self) -> Any:
        return getattr(self._rt, "orchestration_events", None)

    @property
    def run_id(self) -> Any:
        return getattr(self._rt, "run_id", None)

    @property
    def command_submitter(self) -> Any:
        return getattr(self._rt, "command_submitter", None)

    @property
    def crow_ask_router(self) -> Any:
        return getattr(self._rt, "crow_ask_router", None)

    @property
    def sessions(self) -> Any:
        return getattr(self._rt, "sessions", None)

    def register(self, agent: Any) -> None:
        fn = getattr(self._rt, "register_agent", None)
        if callable(fn):
            fn(agent)
            return
        self._fallback.register(agent)

    def record(self, agent: Any) -> None:
        fn = getattr(self._rt, "sync_agent", None)
        if callable(fn):
            fn(agent)
            return
        self._fallback.record(agent)

    async def transition(
        self,
        agent: Any,
        *,
        from_status: AgentStatus | str | None = None,
        to_status: AgentStatus,
        reason: str | None = None,
    ) -> None:
        fn = getattr(self._rt, "transition", None)
        if callable(fn) and _is_configured_callable(fn):
            result = fn(
                agent,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
            if hasattr(result, "__await__"):
                await result
            return
        # Legacy stubs only expose sync_agent — emulate transition via status+record.
        agent.status = to_status
        self.transitions.append((agent, to_status))
        self.record(agent)

    def find(self, agent_id: str) -> Any | None:
        fn = getattr(self._rt, "get_agent", None)
        if callable(fn) and _is_configured_callable(fn):
            return fn(agent_id)
        return self._fallback.find(agent_id)

    def find_crow(self, ticket_id: str) -> Any | None:
        fn = getattr(self._rt, "get_crow", None)
        if callable(fn) and _is_configured_callable(fn):
            return fn(ticket_id)
        return self._fallback.find_crow(ticket_id)

    def find_crow_handler(self, ticket_id: str) -> Any | None:
        fn = getattr(self._rt, "get_crow_handler", None)
        if callable(fn) and _is_configured_callable(fn):
            return fn(ticket_id)
        return self._fallback.find_crow_handler(ticket_id)

    def all(self) -> tuple[Any, ...]:
        return self._fallback.all()

    async def reap(self, agent_id: str) -> None:
        fn = getattr(self._rt, "reap", None)
        if callable(fn):
            result = fn(agent_id)
            if hasattr(result, "__await__"):
                await result
            self.reaped.append(agent_id)
            return
        await self._fallback.reap(agent_id)

    def rename(self, old_agent_id: str, new_agent_id: str) -> Any:
        fn = getattr(self._rt, "rename_agent", None)
        if callable(fn):
            return fn(old_agent_id, new_agent_id)
        return self._fallback.rename(old_agent_id, new_agent_id)

    def emit_lifecycle(self, **_kwargs: Any) -> None:
        return None

    def heartbeat(self, agent_id: str, *, invalidate: bool) -> None:
        del agent_id, invalidate


def _is_configured_callable(fn: Any) -> bool:
    """Skip MagicMock auto-children that were never given a return_value/side_effect."""
    if not isinstance(fn, MagicMock):
        return True
    from unittest.mock import DEFAULT

    return fn._mock_side_effect is not None or fn._mock_return_value is not DEFAULT


def adapt_rt_stub(rt: Any, *, repo_root: Path | None = None) -> Orchestrator:
    """Legacy SimpleNamespace/MagicMock → Orchestrator bridge.

    Prefer ``build_test_orchestrator`` / ``FakeAgents`` for new and migrated
    tests. Retained only for stubs that still mutate ``rt.get_*`` after
    construction (e.g. worktree / recovery reattach fixtures).
    """
    # MagicMock auto-creates attributes, so never trust getattr for cache keys.
    if isinstance(rt, MagicMock):
        agents: Any = _DelegatingAgents(rt)
    else:
        agents = getattr(rt, "_fake_agents", None)
        if agents is None or isinstance(agents, MagicMock):
            agents = _DelegatingAgents(rt)
            try:
                rt._fake_agents = agents
            except Exception:
                pass

    config = getattr(rt, "config", None) or default_test_config()
    if isinstance(config, (SimpleNamespace, MagicMock)) and not isinstance(config, Config):
        project = getattr(config, "project", None)
        project_name = getattr(project, "name", "demo") if project is not None else "demo"
        if isinstance(project_name, MagicMock):
            project_name = "demo"
        runtime = getattr(config, "runtime", None)
        template = (
            getattr(runtime, "session_name_template", "murder_{project}_{role}{suffix}")
            if runtime is not None
            else "murder_{project}_{role}{suffix}"
        )
        if isinstance(template, MagicMock):
            template = "murder_{project}_{role}{suffix}"
        cfg = Config(
            project=ProjectConfig(name=str(project_name)),
            collaborator=HarnessRoleConfig(harness="codex"),
            default_crow=HarnessRoleConfig(harness="codex"),
            crow_handler=CrowHandlerConfig(model="test-model"),
            runtime=RuntimeConfig(session_name_template=str(template)),
        )
    else:
        cfg = config

    root = repo_root or getattr(rt, "repo_root", None)
    if root is None or isinstance(root, MagicMock):
        root = Path("/tmp/murder-test")

    db = getattr(rt, "db", None)
    if isinstance(db, MagicMock):
        db = None
    run_id = getattr(rt, "run_id", None)
    if isinstance(run_id, MagicMock) or run_id is None:
        run_id = "test-run"
    events = getattr(rt, "orchestration_events", None)
    if isinstance(events, MagicMock):
        events = None
    commands = getattr(rt, "command_submitter", None)
    if isinstance(commands, MagicMock):
        commands = None
    event_sink = getattr(rt, "event_sink", None)
    if isinstance(event_sink, MagicMock):
        event_sink = None
    plan_sync = getattr(rt, "plan_sync", None)
    if isinstance(plan_sync, MagicMock):
        plan_sync = None
    user_config = getattr(rt, "user_cfg", None)
    if isinstance(user_config, MagicMock):
        user_config = None

    return build_test_orchestrator(
        repo_root=root,
        db=db,
        config=cfg,
        user_config=user_config,
        agents=agents,
        run_id=str(run_id),
        events=events,
        commands=commands,
        event_sink=event_sink,
        plan_sync=plan_sync,
    )


__all__ = [
    "FakeAgents",
    "build_test_orchestrator",
    "default_test_config",
    "adapt_rt_stub",
]
