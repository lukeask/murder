"""Bootstrap helpers for ACP verified-control sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from murder.llm.harness_control.acp.agents import (
    AcpAgentProfile,
    get_agent,
    get_agent_for_harness,
)
from murder.llm.harness_control.acp.client import AcpClient
from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.adapters.rpc_model_options import (
    plan_acp_model_config_writes,
)


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


def _argv_with_startup_model(
    argv: Sequence[str],
    model: str | None,
) -> tuple[str, ...]:
    """Insert ``--model <id>`` before the ACP subcommand when a model is requested.

    Cursor's reliable model pin is the process argv (``agent --model X acp``);
    ``session/new``'s ``model`` kwarg is ignored. Murder catalog ids (``auto``,
    ``composer-2.5``) are the same ids ``agent --model`` accepts.
    """
    parts = tuple(argv)
    cleaned = (model or "").strip()
    if not cleaned or not parts:
        return parts
    if "--model" in parts:
        return parts
    # profile.argv is typically ``("agent", "acp")`` — insert before the subcommand.
    if len(parts) >= 2:  # noqa: PLR2004
        return (parts[0], "--model", cleaned, *parts[1:])
    return (*parts, "--model", cleaned)


def _split_startup_speed(
    effort: str | None,
) -> tuple[bool | None, str | None]:
    """Map Murder Cursor slow/fast onto ``(fast_enabled, thought_effort)``."""
    if effort in {"slow", "fast"}:
        return effort == "fast", None
    return None, effort


_MAX_MODEL_CONFIG_WRITES = 6


async def apply_acp_model_config(
    connection: AcpConnection,
    *,
    model_id: str | None = None,
    fast_enabled: bool | None = None,
    effort: str | None = None,
) -> list[dict[str, object]]:
    """Apply model / fast / effort via ``session/set_config_option`` writes.

    Re-plans against the live catalog after each write so parameterized
    ``fast`` / ``effort`` / ``reasoning`` options that appear only after a
    model change are still honored.
    """
    catalog = [
        option for option in connection.session_config_options if isinstance(option, dict)
    ]
    session_id = connection.session_id
    if not session_id:
        raise ValueError("apply_acp_model_config requires connection.session_id")

    remaining_model = model_id
    remaining_fast = fast_enabled
    remaining_effort = effort
    applied = 0
    while applied < _MAX_MODEL_CONFIG_WRITES:
        writes = plan_acp_model_config_writes(
            catalog,
            model_id=remaining_model,
            fast_enabled=remaining_fast,
            effort=remaining_effort,
        )
        if not writes:
            break
        config_id, value = writes[0]
        updated = await connection.request(
            "session/set_config_option",
            {
                "sessionId": session_id,
                "configId": config_id,
                "value": value,
            },
        )
        options = updated.get("configOptions") if isinstance(updated, dict) else None
        if isinstance(options, list):
            catalog = [option for option in options if isinstance(option, dict)]
            connection.session_config_options = catalog
            connection.pending_config_options = catalog
        if config_id == "model":
            remaining_model = None
        elif config_id == "fast":
            remaining_fast = None
        else:
            remaining_effort = None
        applied += 1
    return catalog


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
    When ``model`` is set, it is pinned on the process argv and the resulting
    ``configOptions`` are stashed for SelectModel / active-model readback.
    """

    profile = resolve_agent_profile(agent)
    cwd_str = str(cwd)
    base_argv = tuple(argv) if argv is not None else profile.argv
    effective_argv = _argv_with_startup_model(base_argv, model)
    connection = AcpConnection(argv=effective_argv, env=env, cwd=cwd_str)
    if model is not None:
        connection.desired_model = model
    fast_enabled, thought_effort = _split_startup_speed(effort)
    if effort is not None:
        connection.desired_effort = thought_effort if thought_effort is not None else effort
    connection.desired_fast_enabled = fast_enabled
    await connection.start()
    client = AcpClient(connection)
    try:
        await client.initialize(client_capabilities=dict(profile.client_capabilities))
        if profile.auth_method_id is not None:
            await client.authenticate(profile.auth_method_id)
        result = await client.session_new(cwd=cwd_str)
        options = result.get("configOptions") if isinstance(result, dict) else None
        catalog: list[dict[str, object]] = []
        if isinstance(options, list):
            catalog = [option for option in options if isinstance(option, dict)]
        if catalog:
            connection.session_config_options = catalog
            connection.pending_config_options = catalog
        # Cursor ignores session/new ``model`` and is unreliable about argv
        # ``--model`` for ACP; pin model + speed/effort via set_config_option.
        requested = (model or "").strip()
        if requested or fast_enabled is not None or thought_effort is not None:
            await apply_acp_model_config(
                connection,
                model_id=requested or None,
                fast_enabled=fast_enabled,
                effort=thought_effort,
            )
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
    "apply_acp_model_config",
    "placeholder_cmd_for_profile",
    "resolve_agent_profile",
    "start_acp_session",
    "uses_acp_backend",
]
