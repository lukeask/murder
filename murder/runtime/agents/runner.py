"""Factory for spawning agent sessions from an AgentSpec (W4).

CROW_HANDLER has a specialized spawn path in ``orchestration/orchestrator.py``
because it requires ``crow_session`` (the watched crow's tmux session) that
cannot be cleanly expressed via AgentSpec without coupling the spec to a
single role's implementation details.  All other roles go through here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.types import AgentRole
from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.runtime.agents.crow import CrowAgent
from murder.runtime.agents.planning_agent import PlanningAgent
from murder.llm.harnesses import get as get_harness

from murder.runtime.agents.events import AgentEventSink, AgentStartedEvent
from murder.runtime.agents.sessions import AgentHandle, AgentSpec
from murder.runtime.terminal.session_names import SessionNamePolicy


async def spawn_agent(
    spec: AgentSpec,
    *,
    repo_root: Path,
    session_names: SessionNamePolicy,
    agents: AgentRuntime,
    event_sink: AgentEventSink | None = None,
) -> AgentHandle:
    """Instantiate, register, and start an agent from a fully-resolved spec.

    The caller is responsible for business-logic setup: composing the startup
    prompt, resolving the harness kind, and checking for existing sessions.
    This function owns: create session name, instantiate the right class,
    register with AgentRuntime, start the agent, emit AgentStartedEvent, return handle.

    Raises ValueError for CROW_HANDLER (needs crow_session not in spec) and
    any unrecognised role.
    """
    role = spec.role

    if role == AgentRole.CROW:
        if spec.scope.ticket_id is None:
            raise ValueError("CROW spec requires scope.ticket_id")
        if not spec.harness:
            raise ValueError("CROW spec requires harness")
        ticket_id = spec.scope.ticket_id
        session_name = session_names.format("crow", f"_{ticket_id}")
        harness = get_harness(spec.harness, startup_model=spec.model, startup_effort=spec.effort)
        worktree_path = Path(spec.scope.worktree_path) if spec.scope.worktree_path else None
        agent_repo = worktree_path or repo_root
        agent = CrowAgent(
            agent_id=f"crow-{ticket_id}",
            ticket_id=ticket_id,
            session=session_name,
            harness=harness,
            repo_root=agent_repo,
            startup_model=spec.model,
            startup_effort=spec.effort,
            worktree_path=worktree_path,
            additional_workspace_dirs=tuple(
                Path(path) for path in spec.additional_workspace_dirs
            ),
            runtime=agents,
        )

    elif role == AgentRole.COLLABORATOR:
        if not spec.harness:
            raise ValueError("COLLABORATOR spec requires harness")
        session_name = session_names.format("collaborator", "")
        harness = get_harness(spec.harness, startup_model=spec.model, startup_effort=spec.effort)
        agent = CollaboratorAgent(
            agent_id="collaborator-0",
            session=session_name,
            harness=harness,
            repo_root=repo_root,
            startup_model=spec.model,
            startup_effort=spec.effort,
            runtime=agents,
        )

    elif role == AgentRole.PLANNER:
        if spec.scope.plan_name is None:
            raise ValueError("PLANNER spec requires scope.plan_name")
        if not spec.harness:
            raise ValueError("PLANNER spec requires harness")
        plan_name = spec.scope.plan_name
        session_name = session_names.format("planner", f"_{plan_name}")
        harness = get_harness(spec.harness, startup_model=spec.model, startup_effort=spec.effort)
        agent = PlanningAgent(
            agent_id=f"planner-{plan_name}",
            session=session_name,
            plan_name=plan_name,
            harness=harness,
            repo_root=repo_root,
            startup_model=spec.model,
            startup_effort=spec.effort,
            runtime=agents,
        )

    elif role == AgentRole.CROW_HANDLER:
        raise ValueError(
            "CROW_HANDLER requires crow_session; use Orchestrator.spawn_crow_handler directly"
        )

    else:
        raise ValueError(f"spawn_agent: unsupported role {role!r}")

    agents.register(agent)
    try:
        await agent.start(spec.startup_prompt or "", {})
    except BaseException:
        await agents.reap(agent.id)
        raise

    handle = AgentHandle(agent_id=agent.id, session_name=session_name, spec=spec, task=None)

    if event_sink is not None:
        await event_sink.emit(
            AgentStartedEvent(
                session_name=session_name,
                started_at=datetime.now(timezone.utc),
            )
        )

    return handle


__all__ = ["spawn_agent"]
