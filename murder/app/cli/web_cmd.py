"""`murder web up|down|serve` — serve the web/mobile UI over a WebSocket bridge.

`murder web up` daemonizes a long-lived aiohttp server (mirroring how `murder up`
daemonizes the supervisor) that serves the compiled React frontend as static
files and exposes a ``/bus`` WebSocket that relays 1:1 to the murder unix bus.
`murder web down` SIGTERMs it. ``serve`` is the hidden worker the daemon execs.

The supervisor is ensured up first (reusing service_cmd helpers), since the
bridge is useless without the bus socket it relays to.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer

from murder.app.cli._util import pid_is_alive as _pid_is_alive
from murder.app.cli._util import repo_root as _repo_root
from murder.app.cli.service_cmd import _ensure_supervisor_started
from murder.bus.transport_socket import default_socket_path
from murder.state.storage.filesystem import atomic_write_text, read_lock_pid
from murder.state.storage.paths import agents_dir, logs_dir

DEFAULT_PORT = 8473

web_app = typer.Typer(help="Serve the murder web/mobile UI over a WebSocket bridge.")


def _web_lock_path(repo: Path) -> Path:
    """PID file for the web bridge daemon (distinct from the supervisor `.lock`)."""
    return agents_dir(repo) / ".web.lock"


# ---------------------------------------------------------------------------
# Network scope -> bind host
# ---------------------------------------------------------------------------


def _tailscale_ipv4() -> str | None:
    """Return this machine's first Tailscale IPv4 (100.x), or None if unavailable."""
    if shutil.which("tailscale") is None:
        return None
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        addr = line.strip()
        if re.match(r"^100\.\d{1,3}\.\d{1,3}\.\d{1,3}$", addr):
            return addr
    return None


def resolve_host(*, localhost: bool, lan: bool, tailnet: bool) -> tuple[str, str | None]:
    """Map the (mutually exclusive) scope flags to a bind host.

    Returns ``(host, hint)`` where ``hint`` is an optional advisory line to
    print (e.g. the tailscale-serve tip). Raises ``typer.BadParameter`` on
    conflicting flags, or a clear error when tailscale is requested but absent.
    """
    chosen = [name for name, on in (("lan", lan), ("tailnet", tailnet)) if on]
    if len(chosen) > 1:
        raise typer.BadParameter("--localhost, --lan and --tailnet are mutually exclusive.")
    if tailnet:
        ip = _tailscale_ipv4()
        if ip is None:
            raise typer.BadParameter(
                "could not determine a Tailscale IPv4 (is tailscale installed and up? "
                "try `tailscale ip -4`)."
            )
        hint = (
            "tip: `tailscale serve` can reverse-proxy this port for HTTPS on your tailnet."
        )
        return ip, hint
    if lan:
        return "0.0.0.0", None
    # default / explicit --localhost
    return "127.0.0.1", None


def _reachable_hosts(bind_host: str) -> list[str]:
    """User-facing hostnames for a bind host (0.0.0.0 expands to localhost + LAN IP)."""
    if bind_host == "0.0.0.0":
        hosts = ["127.0.0.1"]
        with contextlib.suppress(OSError):
            lan_ip = socket.gethostbyname(socket.gethostname())
            if lan_ip not in hosts:
                hosts.append(lan_ip)
        return hosts
    return [bind_host]


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------


def _web_is_live(repo: Path) -> bool:
    pid = read_lock_pid(_web_lock_path(repo))
    return bool(pid is not None and _pid_is_alive(pid))


def _spawn_web_daemon(repo: Path, host: str, port: int) -> subprocess.Popen[bytes]:
    log_root = logs_dir(repo) / datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_root.mkdir(parents=True, exist_ok=True)
    log_file = open(log_root / "web.ndjson", "ab", buffering=0)
    return subprocess.Popen(
        [sys.executable, "-m", "murder", "web", "serve", "--host", host, "--port", str(port)],
        cwd=str(repo),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )


async def _health_ok(host: str, port: int, *, timeout_s: float = 0.5) -> bool:
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(probe_host, port), timeout=timeout_s
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    del reader
    return True


@web_app.command("up")
def cmd_web_up(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Port to serve on."),
    localhost: bool = typer.Option(
        True, "--localhost/--no-localhost", help="Bind 127.0.0.1 (default)."
    ),
    lan: bool = typer.Option(False, "--lan", help="Bind 0.0.0.0 (reachable on your LAN)."),
    tailnet: bool = typer.Option(
        False, "--tailnet", help="Bind this machine's Tailscale IPv4."
    ),
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run in the foreground (don't daemonize)."
    ),
) -> None:
    """Start the web/mobile bridge server for this repo."""
    repo = _repo_root()
    # --lan/--tailnet override the default --localhost without an explicit --no-localhost.
    host, hint = resolve_host(
        localhost=localhost and not (lan or tailnet), lan=lan, tailnet=tailnet
    )

    if foreground:
        _run_web_foreground(repo, host, port)
        return

    if _web_is_live(repo):
        typer.echo("already up")
        return

    async def _start() -> None:
        await _ensure_supervisor_started(repo, default_socket_path(repo))

    try:
        asyncio.run(_start())
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"could not start supervisor: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    proc = _spawn_web_daemon(repo, host, port)

    async def _await_ready() -> bool:
        for delay in (0.25, 0.5, 0.75, 1.0, 1.0, 1.0):
            await asyncio.sleep(delay)
            if await _health_ok(host, port):
                return True
            if proc.poll() is not None:
                return False
        return False

    ready = asyncio.run(_await_ready())
    if not ready:
        rc = proc.poll()
        detail = f" (worker exited with code {rc})" if rc is not None else ""
        typer.secho(
            f"web bridge did not become ready on port {port}{detail}. "
            f"Check the log under {logs_dir(repo)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("started")
    for reachable in _reachable_hosts(host):
        typer.echo(f"  http://{reachable}:{port}")
    if hint:
        typer.echo(hint)


@web_app.command("down")
def cmd_web_down() -> None:
    """Stop the running web bridge for this repo."""
    repo = _repo_root()
    lock = _web_lock_path(repo)
    pid = read_lock_pid(lock)
    if pid is None or not _pid_is_alive(pid):
        with contextlib.suppress(FileNotFoundError):
            lock.unlink()
        typer.echo("not running")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    with contextlib.suppress(FileNotFoundError):
        lock.unlink()
    typer.echo("stopped")


@web_app.command("serve", hidden=True)
def cmd_web_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Bind port."),
) -> None:
    """Internal worker entrypoint that runs the bridge in-process."""
    _run_web_foreground(_repo_root(), host, port)


def _run_web_foreground(repo: Path, host: str, port: int) -> None:
    """Resolve assets, write the PID file, and run the aiohttp bridge until SIGTERM."""
    from murder.web.bridge import AiohttpMissingError, resolve_assets_dir, run_server

    try:
        assets_dir = resolve_assets_dir(repo)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    socket_path = default_socket_path(repo)
    lock = _web_lock_path(repo)
    lock.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(lock, f"{os.getpid()}\n")

    async def _main() -> None:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        server = asyncio.create_task(
            run_server(host=host, port=port, socket_path=socket_path, assets_dir=assets_dir)
        )
        await stop.wait()
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server

    try:
        asyncio.run(_main())
    except AiohttpMissingError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    finally:
        current = read_lock_pid(lock)
        if current == os.getpid():
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()
