"""Bootstrap helpers for Codex app-server verified-control sessions."""

from __future__ import annotations

import os
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


def default_codex_config_path() -> Path:
    """Return ``$CODEX_HOME/config.toml`` or ``~/.codex/config.toml``."""
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    root = Path(codex_home) if codex_home else Path.home() / ".codex"
    return root / "config.toml"


def read_codex_model_provider(*, config_path: Path | str | None = None) -> str | None:
    """Read top-level ``model_provider`` from Codex user config, if present.

    Only inspects keys before the first ``[section]`` so nested
    ``[model_providers.*]`` tables are ignored. Missing/unreadable config
    yields ``None`` (Codex then keeps its own default, typically openai).
    """
    path = Path(config_path) if config_path is not None else default_codex_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _top_level_toml_string(text, "model_provider")


def resolve_app_server_model_provider(
    model_provider: str | None = None,
    *,
    config_path: Path | str | None = None,
) -> str | None:
    """Prefer an explicit Murder override; else Codex ``model_provider``."""
    if model_provider is not None:
        cleaned = model_provider.strip()
        if cleaned:
            return cleaned
    return read_codex_model_provider(config_path=config_path)


def _top_level_toml_string(text: str, key: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            break
        if not line.startswith(key):
            continue
        rest = line[len(key) :].lstrip()
        if not rest.startswith("="):
            continue
        value = rest[1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = value.strip()
        return value or None
    return None


async def start_app_server_session(
    *,
    cwd: Path | str,
    model: str | None = None,
    model_provider: str | None = None,
    effort: str | None = None,
    argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    codex_config_path: Path | str | None = None,
) -> tuple[AppServerConnection, AppServerClient]:
    """Start ``codex app-server``, initialize, and open a thread.

    ``model_provider`` maps to ``thread/start``'s ``modelProvider``. When
    omitted, Murder falls back to Codex user config (``model_provider``) so
    local providers such as ``local`` are honored without a Murder override.
    OpenAI/cloud stays the default when neither source sets a provider.

    Returns the live connection (with ``thread_id`` set) and a client wrapper.
    """

    cwd_str = str(cwd)
    resolved_provider = resolve_app_server_model_provider(
        model_provider,
        config_path=codex_config_path,
    )
    connection = AppServerConnection(argv=argv, env=env, cwd=cwd_str)
    if model is not None:
        connection.desired_model = model
    if resolved_provider is not None:
        connection.desired_model_provider = resolved_provider
    if effort is not None:
        connection.desired_effort = effort
    await connection.start()
    client = AppServerClient(connection)
    try:
        await client.initialize()
        kwargs: dict[str, Any] = {"cwd": cwd_str}
        if model is not None:
            kwargs["model"] = model
        if resolved_provider is not None:
            kwargs["model_provider"] = resolved_provider
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
    "default_codex_config_path",
    "read_codex_model_provider",
    "resolve_app_server_model_provider",
    "start_app_server_session",
    "uses_codex_app_server_backend",
]
