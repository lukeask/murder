"""Browser entrypoint helpers.

There is no web bridge process: ``DaemonHttpServer`` serves browser assets and
path-scoped WebSocket endpoints from the daemon process itself.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

import typer

from murder.app.cli._util import repo_root as _repo_root
from murder.app.cli.service_cmd import _ensure_supervisor_started
from murder.state.storage.paths import lock_path
from murder.state.storage.service_registry import list_service_sessions, project_session_name

web_app = typer.Typer(help="Open Murder's service-owned browser endpoint.")


def _http_base_from_websocket_url(websocket_url: str) -> str:
    """Map ``ws(s)://host:port/api/ws[/...]`` to the browser origin URL."""
    parts = urlsplit(websocket_url)
    scheme = "https" if parts.scheme == "wss" else "http"
    return urlunsplit((scheme, parts.netloc, "/", "", ""))


def _service_url() -> str:
    repo = _repo_root()
    name = project_session_name(repo)
    session = next((item for item in list_service_sessions() if item.name == name), None)
    if session is None:
        raise typer.BadParameter("service did not publish its application endpoint")
    return _http_base_from_websocket_url(session.websocket_url)


@web_app.command("up")
def cmd_web_up() -> None:
    """Make sure the service is running and print its browser URL."""

    repo = _repo_root()
    asyncio.run(_ensure_supervisor_started(repo, lock_path(repo)))
    typer.echo(_service_url())


@web_app.command("down")
def cmd_web_down() -> None:
    """Browser delivery is part of the service. Use ``murder down`` instead."""

    raise typer.BadParameter("the browser endpoint belongs to the service. Run `murder down`.")
