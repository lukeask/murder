"""TUI launch and service-start commands."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from murder.bus.transport_socket import default_socket_path
from murder.cli.service_cmd import _ensure_supervisor, _ensure_supervisor_started, _run_async_entry
from murder.config import Config
from murder.tui.app import MurderApp
from murder.tui.client import TuiRuntimeClient


def _repo_root() -> Path:
    return Path.cwd().resolve()


async def _launch_tui() -> None:
    repo = _repo_root()
    cfg = Config.load(repo)
    os.environ.setdefault("GIO_USE_VFS", "local")
    os.environ.setdefault("GSETTINGS_BACKEND", "memory")
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "disabled:")
    os.environ.setdefault("NO_AT_BRIDGE", "1")
    socket_path = default_socket_path(repo)
    await _ensure_supervisor(repo, socket_path)
    client = TuiRuntimeClient(repo, socket_path, cfg)
    await client.connect()
    app_ui = MurderApp(client)
    try:
        await app_ui.run_async()
    finally:
        await client.close()


def cmd_up() -> None:
    """Start the background supervisor and print whether it was already running."""

    async def _up() -> None:
        repo = _repo_root()
        started = await _ensure_supervisor_started(repo, default_socket_path(repo))
        typer.echo("started" if started else "already up")

    _run_async_entry(_up())
