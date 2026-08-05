"""Browser entrypoint helpers.

There is no web bridge process: ``DaemonHttpServer`` serves browser assets and
path-scoped WebSocket endpoints from the daemon process itself.
"""

from __future__ import annotations

import typer

from murder.app.cli._util import repo_root as _repo_root
from murder.app.cli.service_cmd import _run_async_entry, ensure_daemon_and_activate
from murder.app.protocol.common import DAEMON_WEBSOCKET_HOST, DAEMON_WEBSOCKET_PORT

web_app = typer.Typer(help="Open Murder's service-owned browser endpoint.")


def daemon_browser_url() -> str:
    """Fixed daemon origin — no per-repo URL resolution."""
    return f"http://{DAEMON_WEBSOCKET_HOST}:{DAEMON_WEBSOCKET_PORT}/"


@web_app.command("up")
def cmd_web_up() -> None:
    """Make sure the daemon is running, activate cwd, and print the browser URL."""

    async def _up() -> None:
        await ensure_daemon_and_activate(_repo_root())
        typer.echo(daemon_browser_url())

    _run_async_entry(_up())


@web_app.command("down")
def cmd_web_down() -> None:
    """Browser delivery is part of the daemon. Use ``murder down`` instead."""

    raise typer.BadParameter("the browser endpoint belongs to the daemon. Run `murder down`.")
