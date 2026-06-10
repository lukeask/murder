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
import tempfile
import uuid
from pathlib import Path

LARGE_PAYLOAD_BYTES = 1024
PASTE_ENTER_DELAY_S = 0.15


class TmuxError(RuntimeError):
    """Non-zero exit from tmux."""


async def _tmux(*args: str, check: bool = True, timeout_s: float = 10) -> tuple[int, str, str]:
    """Run `tmux <args>`; return (rc, stdout, stderr)."""

    proc = await asyncio.create_subprocess_exec(
        "tmux",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        stdout_raw, stderr_raw = b"", b"tmux command timed out"
        returncode = 124
    else:
        returncode = int(proc.returncode or 0)
    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    if check and returncode != 0:
        raise TmuxError(
            f"tmux {' '.join(shlex.quote(a) for a in args)} → rc={returncode}: "
            f"{stderr.strip() or stdout.strip()}"
        )
    return returncode, stdout, stderr


async def session_exists(name: str) -> bool:
    rc, _, _ = await _tmux("has-session", "-t", name, check=False)
    return rc == 0


async def create_session(
    name: str,
    cwd: Path,
    cmd: list[str] | None = None,
    *,
    width: int = 220,
    height: int = 50,
) -> None:
    """Create a detached session in `cwd`, optionally with an initial command.

    Detached sessions otherwise default to 80x24; we never attach a client, so
    the harness CLI renders at whatever size we set here for the session's life.
    80 columns is too narrow for some harness output — e.g. codex `/status`
    wraps the weekly ``(resets … on … May)`` onto a continuation line, which
    breaks single-line parsing — so default generously wide.
    """
    if await session_exists(name):
        raise TmuxError(f"session already exists: {name}")
    args = [
        "new-session",
        "-d",
        "-s",
        name,
        "-x",
        str(width),
        "-y",
        str(height),
        "-c",
        str(cwd),
    ]
    if cmd:
        # tmux treats remaining argv as a command to run inside the new session's pane.
        args.extend(cmd)
    await _tmux(*args)


async def kill_session(name: str) -> None:
    """Kill `name`. No-op if it doesn't exist."""
    if await session_exists(name):
        await _tmux("kill-session", "-t", name)


async def rename_session(old_name: str, new_name: str) -> bool:
    """Rename a tmux session if it exists; return whether a rename happened."""
    if old_name == new_name:
        return False
    if not await session_exists(old_name):
        return False
    if await session_exists(new_name):
        raise TmuxError(f"session already exists: {new_name}")
    await _tmux("rename-session", "-t", old_name, new_name)
    return True


async def list_sessions(prefix: str | None = None) -> list[str]:
    rc, out, _ = await _tmux(
        "list-sessions",
        "-F",
        "#{session_name}",
        check=False,
        timeout_s=0.5,
    )
    if rc != 0:
        return []  # no server running → no sessions
    names = [line for line in out.splitlines() if line]
    return [n for n in names if prefix is None or n.startswith(prefix)]


async def capture_pane(
    name: str, lines: int = 200, *, perf: object | None = None, escapes: bool = False
) -> str:
    """Capture the last `lines` lines from the session's active pane.

    Optional ``perf`` is a duck-typed perf logger (``enabled`` and sync ``span``); used only by
    TUI call sites.

    ``escapes`` adds tmux's ``-e`` flag so SGR (colour/style) escape sequences
    are preserved in the output. Markerless harnesses that colour-code their
    user-input blocks (e.g. cursor) rely on this to recover turn roles; default
    off so the common case stays plain text.
    """
    # -p: pipe to stdout; -S -<n>: start n lines back from the bottom of history;
    # -e: keep SGR escapes (opt-in).
    extra = ("-e",) if escapes else ()
    if perf is not None and getattr(perf, "enabled", False):
        with perf.span("tmux.capture_pane", session=name, lines=int(lines)):  # type: ignore[attr-defined]
            _, out, _ = await _tmux(
                "capture-pane", "-p", *extra, "-t", name, "-S", f"-{int(lines)}"
            )
            return out
    _, out, _ = await _tmux("capture-pane", "-p", *extra, "-t", name, "-S", f"-{int(lines)}")
    return out


async def send_keys(name: str, text: str, *, literal: bool = True, enter: bool = True) -> None:
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
    used_paste_buffer = False
    if len(payload) < LARGE_PAYLOAD_BYTES:
        await _tmux("send-keys", "-t", name, "-l", text)
    else:
        await _paste_buffer_bytes(name, payload)
        used_paste_buffer = True

    if enter:
        if used_paste_buffer:
            await asyncio.sleep(PASTE_ENTER_DELAY_S)
        await _tmux("send-keys", "-t", name, "Enter")


async def _paste_buffer_bytes(name: str, payload: bytes) -> None:
    """``load-buffer`` + ``paste-buffer`` for raw UTF-8 (no trailing Enter)."""

    buf_name = f"murder_{uuid.uuid4().hex[:8]}"
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


async def paste_buffer_literal(name: str, text: str) -> None:
    """Inject ``text`` via paste-buffer regardless of size (never ``send-keys -l``).

    Harnesses that need one keystroke sequence per terminal paste (e.g. Codex
    after bracketed paste) can chunk prompts and call this per chunk.
    """
    await _paste_buffer_bytes(name, text.encode("utf-8"))


async def interrupt(name: str) -> None:
    """Send Ctrl-C to the session's pane without killing the session."""
    await _tmux("send-keys", "-t", name, "C-c")


def attach_command(name: str) -> str:
    """Shell command a user can run to attach to this session."""
    return f"tmux attach -t {shlex.quote(name)}"
