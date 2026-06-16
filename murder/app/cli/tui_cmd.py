"""TUI launch and service-start commands.

The TUI is the Ink (Node) frontend (F8). `murder` brings the daemon up, resolves the bus socket,
then spawns the Node Ink process pointed at that socket via `MURDER_BUS_SOCKET`. The Node side never
re-derives the per-project socket path — it connects to exactly the absolute path it is handed
(Open decision #2). The legacy in-process Textual `MurderApp` has been retired (F10).
"""

from __future__ import annotations

import os
import subprocess
from importlib.resources import files
from pathlib import Path

import typer

from murder.app.cli.service_cmd import (
    _ensure_supervisor,
    _ensure_supervisor_started,
    _run_async_entry,
    apply_client_log_level,
)
from murder.bus.transport_socket import default_socket_path
from murder.app.cli._util import node_major_version as _node_major_version
from murder.app.cli._util import repo_root as _repo_root

# Node runtime floor (current LTS). Ink 5 needs >=18; 20 is the future-proof floor we ship against.
MIN_NODE_MAJOR = 20


class InkLaunchError(RuntimeError):
    """A precondition for launching the Ink TUI is unmet (Node missing/old, deps absent, …).

    Carries a clear, actionable message; the CLI surfaces it via the shared `_run_async_entry`
    handler (RuntimeError → red message + non-zero exit), so we never spawn on failure.
    """


def _require_node() -> None:
    """Ensure a usable Node (>= MIN_NODE_MAJOR) is on PATH, or raise with install guidance."""
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

    1. **Source checkout** — ``<repo>/inktui/src/index.tsx`` present → run ``tsx src/index.tsx``
       from ``inktui/`` using the locally-installed ``tsx`` (a devDependency). Requires
       ``inktui/node_modules`` to be present; a clear, distinct error fires if it is absent.
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
                "via tsx; install the Node deps first: `cd inktui && npm install`, then re-run "
                "`murder`."
            )
        tsx_bin = node_modules / ".bin" / "tsx"
        runner = str(tsx_bin) if tsx_bin.exists() else "tsx"
        return [runner, "src/index.tsx"], inktui_dir

    bundle = files("murder") / "_inktui" / "index.js"
    bundle_path = Path(str(bundle))
    if not bundle_path.exists():
        raise InkLaunchError(
            "No Ink TUI found: neither a source checkout (inktui/src/index.tsx) nor the packaged "
            f"bundle ({bundle_path}) is present. Reinstall murder, or run from a source checkout."
        )
    return ["node", str(bundle_path)], None


def _spawn_ink(argv: list[str], cwd: Path | None, socket_path: Path, project: str) -> int:
    """Spawn the resolved Ink runner against the bus socket, inheriting the tty, and wait.

    The child owns the terminal (inherited stdio) and shares our process group, so ctrl+c reaches
    it directly. We do **not** tear the daemon down on exit — the service is authoritative and keeps
    running, matching the prior in-process launch. Returns the child's exit code.

    `project` is the repo directory name, passed via `MURDER_PROJECT` purely for the top-bar
    branding (`murder · <project>`) — the TUI's own cwd is unreliable (in dev it runs from `inktui/`).
    """
    env = dict(os.environ)
    env["MURDER_BUS_SOCKET"] = str(socket_path)
    env["MURDER_PROJECT"] = project
    proc = subprocess.run(argv, cwd=str(cwd) if cwd is not None else None, env=env, check=False)
    return proc.returncode


async def _launch_tui() -> None:
    repo = _repo_root()
    socket_path = default_socket_path(repo)
    # Resolve the runner and check Node BEFORE bringing the daemon up — fail fast and clearly,
    # without spawning anything, if the host can't run the TUI.
    argv, cwd = _resolve_ink_entrypoint(repo)
    _require_node()
    await _ensure_supervisor(repo, socket_path)
    _spawn_ink(argv, cwd, socket_path, repo.name)


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
    """Start the background supervisor and print whether it was already running."""
    # Resolve + propagate the rung to env BEFORE spawning serviced (inherited env
    # carries it; the recorder mode rides the same rung — no separate flag).
    apply_client_log_level(log_level)

    async def _up() -> None:
        repo = _repo_root()
        started = await _ensure_supervisor_started(repo, default_socket_path(repo))
        typer.echo("started" if started else "already up")

    _run_async_entry(_up())
