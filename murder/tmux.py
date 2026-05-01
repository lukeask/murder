"""Async wrappers around the `tmux` CLI.

All functions operate on a single tmux server (the user's). Session names
follow `murder_<project>_<role>{_<ticket>}` (config.RuntimeConfig).

Per D10: payloads >LARGE_PAYLOAD_BYTES use `load-buffer`/`paste-buffer`;
below that, `send-keys -l <literal>` is fine. The boundary saves seconds
on big ticket-startup prompts (5–10KB combined system+ticket prompts).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

LARGE_PAYLOAD_BYTES = 1024


class TmuxError(RuntimeError):
    """Non-zero exit from tmux."""


async def _tmux(*args: str, check: bool = True) -> tuple[int, str, str]:
    """Run `tmux <args>` in a worker thread; return (rc, stdout, stderr)."""

    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    proc = await asyncio.to_thread(_run)
    if check and proc.returncode != 0:
        raise TmuxError(
            f"tmux {' '.join(shlex.quote(a) for a in args)} → rc={proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.returncode, proc.stdout, proc.stderr


async def session_exists(name: str) -> bool:
    rc, _, _ = await _tmux("has-session", "-t", name, check=False)
    return rc == 0


async def create_session(name: str, cwd: Path, cmd: list[str] | None = None) -> None:
    """Create a detached session in `cwd`, optionally with an initial command."""
    if await session_exists(name):
        raise TmuxError(f"session already exists: {name}")
    args = ["new-session", "-d", "-s", name, "-c", str(cwd)]
    if cmd:
        # tmux treats remaining argv as a command to run inside the new session's pane.
        args.extend(cmd)
    await _tmux(*args)


async def kill_session(name: str) -> None:
    """Kill `name`. No-op if it doesn't exist."""
    if await session_exists(name):
        await _tmux("kill-session", "-t", name)


async def list_sessions(prefix: str | None = None) -> list[str]:
    rc, out, _ = await _tmux("list-sessions", "-F", "#{session_name}", check=False)
    if rc != 0:
        return []  # no server running → no sessions
    names = [line for line in out.splitlines() if line]
    return [n for n in names if prefix is None or n.startswith(prefix)]


async def capture_pane(name: str, lines: int = 200) -> str:
    """Capture the last `lines` lines from the session's active pane."""
    # -p: pipe to stdout; -S -<n>: start n lines back from the bottom of history.
    _, out, _ = await _tmux("capture-pane", "-p", "-t", name, "-S", f"-{int(lines)}")
    return out


async def send_keys(
    name: str, text: str, *, literal: bool = True, enter: bool = True
) -> None:
    """Deliver `text` to the session's input buffer.

    Small payloads → `tmux send-keys -t <name> -l <text>` (literal).
    Large payloads → `load-buffer` / `paste-buffer` (no shell quoting limits;
    fast for multi-KB strings).
    """
    if not literal:
        # Non-literal: caller is sending key names like 'C-c' or 'Enter'.
        await _tmux("send-keys", "-t", name, text)
        return

    payload = text.encode("utf-8")
    if len(payload) < LARGE_PAYLOAD_BYTES:
        await _tmux("send-keys", "-t", name, "-l", text)
    else:
        buf_name = f"murder_{uuid.uuid4().hex[:8]}"
        # NamedTemporaryFile so we get cleanup on exception paths.
        fd, tmp_name = tempfile.mkstemp(prefix="murder_buf_", suffix=".txt")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
            await _tmux("load-buffer", "-b", buf_name, tmp_name)
            # -d deletes the buffer after paste so we don't leak buffer slots.
            await _tmux("paste-buffer", "-d", "-t", name, "-b", buf_name)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    if enter:
        await _tmux("send-keys", "-t", name, "Enter")


async def interrupt(name: str) -> None:
    """Send Ctrl-C to the session's pane without killing the session."""
    await _tmux("send-keys", "-t", name, "C-c")


def attach_command(name: str) -> str:
    """Shell command a user can run to attach to this session."""
    return f"tmux attach -t {shlex.quote(name)}"
