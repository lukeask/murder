"""Async wrappers around the `tmux` CLI.

All functions operate on a single tmux server (the user's). Session names
follow `murder_<project>_<role>{_<ticket>}` (config.RuntimeConfig).

Per D10: payloads >LARGE_PAYLOAD_BYTES use `load-buffer`/`paste-buffer`.
Below that, `send-keys -l <literal>` is fine. The boundary saves seconds
on big ticket-startup prompts (5–10KB combined system+ticket prompts).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shlex
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from murder.observability.advanced_log import TmuxFrameRecord, current_advanced_log

_log = logging.getLogger(__name__)

LARGE_PAYLOAD_BYTES = 1024
BYTE_SEND_CHUNK_SIZE = 512
PASTE_ENTER_DELAY_S = 0.15
TMUX_TIMEOUT_RETURN_CODE = 124
PANE_DIMENSION_FIELD_COUNT = 2
PANE_TERMINAL_STATE_FIELD_COUNT = 13


@dataclass(frozen=True)
class PaneTerminalState:
    """Read-only tmux screen metadata used to seed a native output stream."""

    columns: int
    rows: int
    alternate: bool
    cursor_x: int
    cursor_y: int
    cursor_visible: bool
    application_cursor: bool
    application_keypad: bool
    insert: bool
    origin: bool
    wraparound: bool
    scroll_top: int
    scroll_bottom: int


class TmuxError(RuntimeError):
    """Non-zero exit from tmux."""


async def _tmux(*args: str, check: bool = True, timeout_s: float = 10) -> tuple[int, str, str]:
    """Run `tmux <args>`. Return (rc, stdout, stderr)."""

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
    try:
        await _tmux(*args)
    except TmuxError as exc:
        # The has-session pre-check is not atomic: a concurrent caller (or a
        # respawning sweeper) can create the same name in the window between the
        # check and new-session. tmux serializes and fails with "duplicate
        # session". Surface that as the intended "already exists" error.
        if "duplicate session" in str(exc).lower():
            raise TmuxError(f"session already exists: {name}") from exc
        raise
    current_advanced_log().record_tmux_frame(
        TmuxFrameRecord(session=name, op="create", meta={"width": width, "height": height})
    )


async def kill_session(name: str) -> None:
    """Kill `name`. No-op if it does not exist."""
    if await session_exists(name):
        await _tmux("kill-session", "-t", name)
        current_advanced_log().record_tmux_frame(TmuxFrameRecord(session=name, op="kill"))


async def resize_session(name: str, *, columns: int, rows: int) -> None:
    """Resize a detached session through the controller-owned mutation path."""

    if columns < 1 or rows < 1:
        raise ValueError("terminal dimensions must be positive")
    await _tmux("resize-window", "-t", name, "-x", str(columns), "-y", str(rows))


async def rename_session(old_name: str, new_name: str) -> bool:
    """Rename a tmux session if it exists. Return whether a rename happened."""
    if old_name == new_name:
        return False
    if not await session_exists(old_name):
        return False
    if await session_exists(new_name):
        raise TmuxError(f"session already exists: {new_name}")
    try:
        await _tmux("rename-session", "-t", old_name, new_name)
    except TmuxError as exc:
        # Same check-then-act race as create_session: another caller may have
        # claimed new_name between the pre-check and rename.
        if "duplicate session" in str(exc).lower():
            raise TmuxError(f"session already exists: {new_name}") from exc
        raise
    return True


async def list_sessions(prefix: str | None = None) -> list[str]:
    rc, out, _ = await _tmux(
        "list-sessions",
        "-F",
        "#{session_name}",
        check=False,
        timeout_s=2.0,
    )
    if rc == TMUX_TIMEOUT_RETURN_CODE:
        # A timeout is NOT "no sessions" — returning [] here would let callers
        # wrongly conclude a murder-owned session is gone and respawn/duplicate.
        raise TmuxError("tmux list-sessions timed out")
    if rc != 0:
        return []  # no server running → no sessions
    names = [line for line in out.splitlines() if line]
    result = [n for n in names if prefix is None or n.startswith(prefix)]
    current_advanced_log().record_tmux_frame(
        TmuxFrameRecord(session="*", op="list", meta={"prefix": prefix, "count": len(result)})
    )
    return result


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
    # -p: pipe to stdout. -S -<n>: start n lines back from the bottom of history.
    # -e: keep SGR escapes (opt-in).
    extra = ("-e",) if escapes else ()
    if perf is not None and getattr(perf, "enabled", False):
        with perf.span("tmux.capture_pane", session=name, lines=int(lines)):  # type: ignore[attr-defined]
            _, out, _ = await _tmux(
                "capture-pane", "-p", *extra, "-t", name, "-S", f"-{int(lines)}"
            )
            _record_capture(name, out, lines=int(lines), escapes=escapes)
            return out
    _, out, _ = await _tmux("capture-pane", "-p", *extra, "-t", name, "-S", f"-{int(lines)}")
    _record_capture(name, out, lines=int(lines), escapes=escapes)
    return out


async def capture_viewport(name: str, *, escapes: bool = False) -> str:
    """Capture only the pane's current screen, excluding scrollback history."""

    extra = ("-e",) if escapes else ()
    _, out, _ = await _tmux("capture-pane", "-p", *extra, "-t", name, "-S", "0")
    _record_capture(name, out, lines=0, escapes=escapes)
    return out


async def capture_saved_viewport(name: str, *, escapes: bool = False) -> str:
    """Capture the screen selected by tmux's read-only ``capture-pane -a``."""

    extra = ("-e",) if escapes else ()
    _, out, _ = await _tmux("capture-pane", "-p", "-a", *extra, "-t", name, "-S", "0")
    return out


async def pane_dimensions(name: str) -> tuple[int, int]:
    """Return the active pane dimensions for immutable frame provenance.

    Reading dimensions is intentionally separate from input delivery: frame
    consumers need renderer context to interpret wrapping, but this operation
    cannot mutate the terminal or imply anything about harness state.
    """

    _, out, _ = await _tmux("display-message", "-p", "-t", name, "#{pane_width} #{pane_height}")
    fields = out.strip().split()
    if len(fields) != PANE_DIMENSION_FIELD_COUNT:
        raise TmuxError(f"tmux returned invalid pane dimensions for {name!r}: {out!r}")
    try:
        width, height = (int(field) for field in fields)
    except ValueError as exc:
        raise TmuxError(f"tmux returned non-integer pane dimensions for {name!r}: {out!r}") from exc
    if width <= 0 or height <= 0:
        raise TmuxError(f"tmux returned non-positive pane dimensions for {name!r}: {out!r}")
    return width, height


async def pane_terminal_state(name: str) -> PaneTerminalState:
    """Read the current screen/cursor modes without attaching or resizing."""

    template = (
        "#{pane_width} #{pane_height} #{alternate_on} #{cursor_x} #{cursor_y} "
        "#{cursor_flag} #{keypad_cursor_flag} #{keypad_flag} "
        "#{insert_flag} #{origin_flag} #{wrap_flag} "
        "#{scroll_region_upper} #{scroll_region_lower}"
    )
    _, out, _ = await _tmux("display-message", "-p", "-t", name, template)
    fields = out.strip().split()
    if len(fields) != PANE_TERMINAL_STATE_FIELD_COUNT:
        raise TmuxError(f"tmux returned invalid terminal state for {name!r}: {out!r}")
    try:
        values = [int(field) for field in fields]
    except ValueError as exc:
        raise TmuxError(f"tmux returned non-integer terminal state for {name!r}: {out!r}") from exc
    columns, rows = values[:2]
    if columns < 1 or rows < 1:
        raise TmuxError(f"tmux returned invalid terminal dimensions for {name!r}: {out!r}")
    return PaneTerminalState(
        columns=columns,
        rows=rows,
        alternate=bool(values[2]),
        cursor_x=values[3],
        cursor_y=values[4],
        cursor_visible=bool(values[5]),
        application_cursor=bool(values[6]),
        application_keypad=bool(values[7]),
        insert=bool(values[8]),
        origin=bool(values[9]),
        wraparound=bool(values[10]),
        scroll_top=values[11],
        scroll_bottom=values[12],
    )


def _record_capture(name: str, out: str, *, lines: int, escapes: bool) -> None:
    """Flight-recorder seam for a successful pane capture (boundary #2).

    The sha1 of the captured text drives the writer's ChangeGate (~1/s sample +
    identical-frame dedup). Unconditional + non-blocking by contract.
    """
    dedup_hash = hashlib.sha1(out.encode("utf-8", errors="replace")).hexdigest()
    current_advanced_log().record_tmux_frame(
        TmuxFrameRecord(
            session=name,
            op="capture",
            frame=out,
            meta={"lines": lines, "escapes": escapes},
            dedup_hash=dedup_hash,
        )
    )


async def send_keys(name: str, text: str, *, literal: bool = True, enter: bool = True) -> None:
    """Deliver `text` to the session's input buffer.

    Small payloads → `tmux send-keys -t <name> -l <text>` (literal).
    Large payloads → `load-buffer` / `paste-buffer` (no shell quoting limits;
    fast for multi-KB strings).
    """
    if not literal:
        # Non-literal: caller is sending key names like 'C-c' or 'Enter'.
        await _tmux("send-keys", "-t", name, text)
        current_advanced_log().record_tmux_frame(
            TmuxFrameRecord(
                session=name, op="send", frame=text, meta={"literal": False, "enter": enter}
            )
        )
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

    current_advanced_log().record_tmux_frame(
        TmuxFrameRecord(session=name, op="send", frame=text, meta={"literal": True, "enter": enter})
    )


async def send_bytes(name: str, payload: bytes) -> None:
    """Write exact terminal bytes to a tmux pane.

    ``send-keys -l`` receives a Unicode argv value and therefore cannot
    truthfully be the transport for arbitrary terminal bytes.  ``-H`` sends
    each supplied hexadecimal key byte verbatim, including control and escape
    bytes.  Chunking bounds argv size while preserving the one-command-per-
    batch fast path for ordinary typing.

    This intentionally does *not* use ``paste-buffer``: tmux paste semantics
    may add bracketed-paste framing or otherwise treat controls differently
    from ordinary keystrokes.
    """

    for offset in range(0, len(payload), BYTE_SEND_CHUNK_SIZE):
        chunk = payload[offset : offset + BYTE_SEND_CHUNK_SIZE]
        await _tmux(
            "send-keys",
            "-t",
            name,
            "-H",
            *(f"{byte:02x}" for byte in chunk),
        )
    current_advanced_log().record_tmux_frame(
        TmuxFrameRecord(
            session=name,
            op="send_bytes",
            # Do not record a user's raw terminal data in the flight recorder.
            meta={
                "bytes": len(payload),
                "chunks": (len(payload) + BYTE_SEND_CHUNK_SIZE - 1) // BYTE_SEND_CHUNK_SIZE,
            },
        )
    )


async def _paste_buffer_bytes(name: str, payload: bytes) -> None:
    """``load-buffer`` + ``paste-buffer`` for raw UTF-8 (no trailing Enter)."""

    buf_name = f"murder_{uuid.uuid4().hex[:8]}"
    fd, tmp_name = tempfile.mkstemp(prefix="murder_buf_", suffix=".txt")
    loaded = False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        await _tmux("load-buffer", "-b", buf_name, tmp_name)
        loaded = True
        # -d deletes the buffer after paste so we do not leak buffer slots.
        await _tmux("paste-buffer", "-d", "-t", name, "-b", buf_name)
        loaded = False
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        # If load-buffer succeeded but paste-buffer -d never fired (e.g. the
        # session died between the two calls), the named buffer leaks a slot for
        # the life of the tmux server. Delete it explicitly.
        if loaded:
            with contextlib.suppress(TmuxError):
                await _tmux("delete-buffer", "-b", buf_name, check=False)


async def paste_buffer_literal(name: str, text: str) -> None:
    """Inject ``text`` via paste-buffer regardless of size (never ``send-keys -l``).

    Harnesses that need one keystroke sequence per terminal paste (e.g. Codex
    after bracketed paste) can chunk prompts and call this per chunk.
    """
    await _paste_buffer_bytes(name, text.encode("utf-8"))


async def interrupt(name: str) -> None:
    """Send Ctrl-C to the session's pane without killing the session."""
    await _tmux("send-keys", "-t", name, "C-c")


async def clear_history(name: str) -> None:
    """Drop a session's scrollback (tmux clear-history).

    Used after dismissing codex's startup update menu so the dismissed menu
    can't be re-captured from history and re-poison idle detection.
    """
    rc, _out, err = await _tmux("clear-history", "-t", name, check=False)
    if rc != 0:
        _log.warning("tmux clear-history failed for %s (rc=%s): %s", name, rc, err.strip())


def attach_command(name: str) -> str:
    """Shell command a user can run to attach to this session."""
    return f"tmux attach -t {shlex.quote(name)}"
