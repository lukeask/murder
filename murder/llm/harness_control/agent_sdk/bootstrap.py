"""Bootstrap helpers for Claude Agent SDK verified-control sessions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from murder.llm.harness_control.agent_sdk.client import AgentSdkClient
from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection

# Placeholder pane so Murder still owns a tmux session name / agent identity.
AGENT_SDK_PLACEHOLDER_CMD: list[str] = [
    "bash",
    "-lc",
    "printf 'murder: claude agent-sdk\\n'; exec sleep infinity",
]


async def start_agent_sdk_session(
    *,
    cwd: Path | str,
    model: str | None = None,
    effort: str | None = None,
    env: Mapping[str, str] | None = None,
    cli_path: str | None = None,
    permission_mode: str = "default",
) -> tuple[AgentSdkConnection, AgentSdkClient]:
    """Start a Claude Agent SDK client session (connect + message reader).

    Returns the live connection and a client wrapper. The SDK subprocess is
    owned by the connection; callers must ``aclose()`` it on shutdown.
    """

    cwd_str = str(cwd)
    connection = AgentSdkConnection(
        cwd=cwd_str,
        model=model,
        effort=effort,
        env=env,
        cli_path=cli_path,
        permission_mode=permission_mode,
    )
    if model is not None:
        connection.desired_model = model
    if effort is not None:
        connection.desired_effort = effort
    try:
        await connection.start()
    except Exception:
        await connection.aclose()
        raise
    return connection, AgentSdkClient(connection)


def uses_claude_agent_sdk_backend(*, harness_kind: str, backend: str | None) -> bool:
    return harness_kind == "claude_code" and backend == "agent_sdk"


__all__ = [
    "AGENT_SDK_PLACEHOLDER_CMD",
    "start_agent_sdk_session",
    "uses_claude_agent_sdk_backend",
]
