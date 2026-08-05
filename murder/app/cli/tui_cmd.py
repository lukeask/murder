"""TUI launch and service-start commands.

The TUI is the Ink (Node) frontend. `murder` brings the daemon up, activates the
cwd repository, then passes ``ws://127.0.0.1:62077/api/ws/{repository_id}`` to
Ink. There is no Unix application transport or fallback protocol.
"""

from __future__ import annotations

import os
import subprocess
from importlib.resources import files
from pathlib import Path

import typer

from murder.app.cli._util import node_major_version as _node_major_version
from murder.app.cli._util import repo_root as _repo_root
from murder.app.cli.service_cmd import (
    _run_async_entry,
    apply_client_log_level,
    ensure_daemon_and_activate,
)
from murder.app.protocol.common import DAEMON_WEBSOCKET_HOST, DAEMON_WEBSOCKET_PORT

# Node runtime floor (current LTS). Ink 5 needs >=18. 20 is the future-proof floor we ship against.
MIN_NODE_MAJOR = 20


class InkLaunchError(RuntimeError):
    """A precondition for launching the Ink TUI is unmet (Node missing/old, deps absent, …).

    Carries a clear, actionable message. The CLI surfaces it via the shared `_run_async_entry`
    handler (RuntimeError → red message + non-zero exit), so we never spawn on failure.
    """


def _require_node() -> None:
    """Make sure a usable Node (>= MIN_NODE_MAJOR) is on PATH, or raise with install guidance."""
    major = _node_major_version()
    have = "none" if major is None else str(major)
    if major is None or major < MIN_NODE_MAJOR:
        raise InkLaunchError(
            f"murder's TUI needs Node >= {MIN_NODE_MAJOR} (you have {have}). "
            f"Install via nvm (`nvm install {MIN_NODE_MAJOR}`) or your distro's NodeSource repo, "
            "then re-run `murder`."
        )


def _resolve_ink_entrypoint(repo: Path) -> tuple[list[str], Path | None]:
    """Resolve how to invoke the Ink runner, probing dev → installed (Open decision build strategy).

    Returns ``(argv, cwd)`` where ``argv`` is the command to spawn and ``cwd`` is the working
    directory (or ``None`` to inherit the current one).

    1. **Source checkout** — the Ink source entrypoint is present → run ``tsx`` with the
       TUI's development tsconfig from its package directory. That config resolves shared-package
       imports directly to workspace source. Requires ``inktui/node_modules`` to be present. A
       clear, distinct error fires if it is absent.
    2. **Installed wheel** — else the packaged self-contained bundle at
       ``importlib.resources``→ ``murder/_inktui/index.js`` → ``node <that path>``.
    """
    inktui_dir = repo / "inktui"
    src_entry = inktui_dir / "src" / "index.tsx"
    if src_entry.exists():
        node_modules = inktui_dir / "node_modules"
        if not node_modules.is_dir():
            raise InkLaunchError(
                f"inktui/node_modules is missing at {node_modules}. The dev TUI runs from source "
                "via tsx. Install the Node workspace first: `npm ci` from the repository root, then re-run "
                "`murder`."
            )
        tsx_bin = node_modules / ".bin" / "tsx"
        runner = str(tsx_bin) if tsx_bin.exists() else "tsx"
        return [runner, "--tsconfig", "tsconfig.dev.json", "src/index.tsx"], inktui_dir

    bundle = files("murder") / "_inktui" / "index.js"
    bundle_path = Path(str(bundle))
    if not bundle_path.exists():
        raise InkLaunchError(
            "No Ink TUI found: neither a source checkout (inktui/src/index.tsx) nor the packaged "
            f"bundle ({bundle_path}) is present. Reinstall murder, or run from a source checkout."
        )
    return ["node", str(bundle_path)], None


def _spawn_ink(
    argv: list[str],
    cwd: Path | None,
    websocket_url: str,
    project: str,
    *,
    daemon_url: str,
) -> int:
    """Spawn the resolved Ink runner against the service WebSocket, inheriting the tty, and wait.

    The child owns the terminal (inherited stdio) and shares our process group, so ctrl+c reaches
    it directly. We do **not** tear the daemon down on exit — the service is authoritative and keeps
    running, matching the prior in-process launch. Returns the child's exit code.

    `project` is the repo directory name, passed via `MURDER_PROJECT` as the initial top-bar
    branding (`murder · <project>`) — the TUI's own cwd is unreliable (in dev it runs from
    `inktui/`). After an in-TUI repo switch the active repo name replaces this seed.

    `daemon_url` is the HTTP base for the in-TUI repo picker (`GET /api/repos`).
    """
    env = dict(os.environ)
    env["MURDER_APPLICATION_WS_URL"] = websocket_url
    env["MURDER_DAEMON_URL"] = daemon_url
    env["MURDER_PROJECT"] = project
    proc = subprocess.run(argv, cwd=str(cwd) if cwd is not None else None, env=env, check=False)
    return proc.returncode


async def _launch_tui() -> None:
    repo = _repo_root()
    # Resolve the runner and check Node BEFORE bringing the daemon up — fail fast and clearly,
    # without spawning anything, if the host can't run the TUI.
    argv, cwd = _resolve_ink_entrypoint(repo)
    _require_node()
    _started, info = await ensure_daemon_and_activate(repo)
    del _started
    websocket_url = info.get("websocket_url")
    if not isinstance(websocket_url, str) or not websocket_url:
        raise InkLaunchError(
            "daemon activated the repository but did not return a WebSocket URL"
        )
    _spawn_ink(
        argv,
        cwd,
        websocket_url,
        repo.name,
        daemon_url=f"http://{DAEMON_WEBSOCKET_HOST}:{DAEMON_WEBSOCKET_PORT}",
    )


def cmd_up(
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help=(
            "Verbosity ladder (one knob): error, warning, info (default), debug, "
            "advanced (flight recorder, redacted), advanced-raw (unredacted)."
        ),
        case_sensitive=False,
    ),
) -> None:
    """Start the background daemon, activate cwd, and print whether it was already running."""
    # Resolve + propagate the rung to env BEFORE spawning serviced (inherited env
    # carries it. The recorder mode rides the same rung. No separate flag.
    apply_client_log_level(log_level)

    async def _up() -> None:
        repo = _repo_root()
        started, _info = await ensure_daemon_and_activate(repo)
        typer.echo("started" if started else "already up")

    _run_async_entry(_up())
