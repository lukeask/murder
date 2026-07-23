"""Bootstrap helpers for ACP verified-control sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from murder.llm.harness_control.acp.agents import (
    AcpAgentProfile,
    get_agent,
    get_agent_for_harness,
)
from murder.llm.harness_control.acp.client import AcpClient
from murder.llm.harness_control.acp.connection import AcpConnection


def resolve_agent_profile(agent: str | AcpAgentProfile) -> AcpAgentProfile:
    """Resolve an agent id string or profile object to an :class:`AcpAgentProfile`."""
    if isinstance(agent, AcpAgentProfile):
        return agent
    return get_agent(agent)


def placeholder_cmd_for_profile(profile: AcpAgentProfile) -> list[str] | None:
    """Return a tmux placeholder command list from the profile, if configured."""
    if profile.placeholder_cmd is None:
        return None
    return list(profile.placeholder_cmd)


async def start_acp_session(
    *,
    agent: str | AcpAgentProfile,
    cwd: Path | str,
    model: str | None = None,
    effort: str | None = None,
    env: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
) -> tuple[AcpConnection, AcpClient]:
    """Start an ACP agent process, initialize, authenticate if needed, and ``session/new``.

    ``agent`` is either a registered agent id (e.g. ``\"cursor\"``) or an
    :class:`AcpAgentProfile`. Optional ``argv`` overrides the profile argv.
    """

    profile = resolve_agent_profile(agent)
    cwd_str = str(cwd)
    effective_argv = tuple(argv) if argv is not None else profile.argv
    connection = AcpConnection(argv=effective_argv, env=env, cwd=cwd_str)
    if model is not None:
        connection.desired_model = model
    if effort is not None:
        connection.desired_effort = effort
    await connection.start()
    client = AcpClient(connection)
    try:
        await client.initialize(client_capabilities=dict(profile.client_capabilities))
        if profile.auth_method_id is not None:
            await client.authenticate(profile.auth_method_id)
        kwargs: dict[str, Any] = {}
        if model is not None:
            kwargs["model"] = model
        await client.session_new(cwd=cwd_str, **kwargs)
    except Exception:
        await connection.aclose()
        raise
    return connection, client


def uses_acp_backend(*, harness_kind: str, backend: str | None) -> bool:
    """True when ``harness_kind`` has a registered ACP agent and ``backend == \"acp\"``."""
    if backend != "acp":
        return False
    return get_agent_for_harness(harness_kind) is not None


__all__ = [
    "placeholder_cmd_for_profile",
    "resolve_agent_profile",
    "start_acp_session",
    "uses_acp_backend",
]
