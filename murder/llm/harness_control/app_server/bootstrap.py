"""Bootstrap helpers for Codex app-server verified-control sessions."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from murder.llm.harness_control.app_server.client import AppServerClient
from murder.llm.harness_control.app_server.connection import AppServerConnection

# Placeholder pane so Murder still owns a tmux session name / agent identity.
APP_SERVER_PLACEHOLDER_CMD: list[str] = [
    "bash",
    "-lc",
    "printf 'murder: codex app-server\\n'; exec sleep infinity",
]


async def start_app_server_session(
    *,
    cwd: Path | str,
    model: str | None = None,
    effort: str | None = None,
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[AppServerConnection, AppServerClient]:
    """Start ``codex app-server``, initialize, and open a thread.

    Returns the live connection (with ``thread_id`` set) and a client wrapper.
    """

    cwd_str = str(cwd)
    connection = AppServerConnection(argv=argv, env=env, cwd=cwd_str)
    if model is not None:
        connection.desired_model = model
    if effort is not None:
        connection.desired_effort = effort
    await connection.start()
    client = AppServerClient(connection)
    try:
        await client.initialize()
        kwargs: dict[str, Any] = {"cwd": cwd_str}
        if model is not None:
            kwargs["model"] = model
        # effort is applied on turn/start via desired_effort; thread/start may
        # accept model only depending on schema version.
        await client.thread_start(**kwargs)
    except Exception:
        await connection.aclose()
        raise
    return connection, client


def uses_codex_app_server_backend(*, harness_kind: str, backend: str | None) -> bool:
    return harness_kind == "codex" and backend == "app_server"


__all__ = [
    "APP_SERVER_PLACEHOLDER_CMD",
    "start_app_server_session",
    "uses_codex_app_server_backend",
]
