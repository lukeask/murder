"""Read-only, ordered tmux output streams and their canonical VT state."""

# Control-mode escaping is specified in terms of byte values.
# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from murder.runtime.terminal import tmux
from murder.runtime.terminal.vt import VtEmulator, VtSnapshot
from murder.state.persistence.connection import RepoDb

LOGGER = logging.getLogger(__name__)
_KEYFRAME_INTERVAL_S = 2.0
_RETAINED_UPDATES = 512


@dataclass(frozen=True)
class TerminalOutputUpdate:
    """One globally sequenced update for a persisted terminal session."""

    sequence: int
    kind: Literal["chunk", "keyframe"]
    captured_at: datetime
    data: bytes | None = None
    snapshot: VtSnapshot | None = None


class TmuxTerminalOutput:
    """One read-only tmux control-mode client and a persistent VT emulator.

    ``tmux -C attach-session -r`` is deliberately a read-only, ignore-size
    client.  It neither sends keys nor changes the detached harness's 220x50
    geometry, unlike attaching an ordinary terminal client.  tmux control mode
    emits `%output` records in pane order. Their escaped payload is decoded
    back to the exact bytes before it reaches the emulator or wire stream.
    """

    def __init__(self, session_name: str) -> None:
        self.session_name = session_name
        self._emulator: VtEmulator | None = None
        self._sequence = 0
        self._updates: deque[TerminalOutputUpdate] = deque(maxlen=_RETAINED_UPDATES)
        self._condition = asyncio.Condition()
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        # capture-pane is only a bootstrap snapshot. It cannot share an atomic
        # boundary with a newly attached control client, so consumers receive a
        # gap before their first native keyframe rather than being told a false
        # raw-history continuity story.
        self.bootstrap_may_have_gap = True

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        pane = await tmux.pane_terminal_state(self.session_name)
        self._emulator = VtEmulator(pane.columns, pane.rows)
        # A control client only receives future %output records. Bootstrap with
        # both tmux screen stores plus its cursor/mode metadata, then publish a
        # canonical keyframe. This is the only lossy path and is never used for
        # ordered chunks.
        with contextlib.suppress(tmux.TmuxError):
            current = await tmux.capture_viewport(self.session_name, escapes=True)
            if pane.alternate:
                primary = await tmux.capture_saved_viewport(self.session_name, escapes=True)
                _feed_capture(self._emulator, primary)
                self._emulator.feed(b"\x1b[?1049h")
            _feed_capture(self._emulator, current)
            _restore_tmux_screen_state(self._emulator, pane)
        await self._publish_keyframe()
        self._task = asyncio.create_task(
            self._run_control_client(), name=f"murder-tmux-output-{self.session_name}"
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        async with self._condition:
            self._condition.notify_all()

    async def updates_after(self, sequence: int) -> tuple[TerminalOutputUpdate, ...]:
        async with self._condition:
            if self._updates and sequence < self._updates[0].sequence - 1:
                # The caller has fallen beyond the retained raw stream. Its
                # next keyframe is authoritative, so do not silently invent a
                # partial replay.
                return (self._updates[-1],)
            return tuple(item for item in self._updates if item.sequence > sequence)

    async def wait_for_update(self, sequence: int) -> TerminalOutputUpdate | None:
        async with self._condition:
            await self._condition.wait_for(lambda: self._closed or self._sequence > sequence)
            if self._closed:
                return None
            for item in self._updates:
                if item.sequence > sequence:
                    return item
            return None

    async def keyframe(self) -> TerminalOutputUpdate:
        """Return a freshly sequenced full state for attach/resync recovery."""

        return await self._publish_keyframe()

    async def _publish_keyframe(self) -> TerminalOutputUpdate:
        assert self._emulator is not None
        async with self._condition:
            self._sequence += 1
            update = TerminalOutputUpdate(
                sequence=self._sequence,
                kind="keyframe",
                captured_at=datetime.now(timezone.utc),
                snapshot=self._emulator.snapshot(),
            )
            self._updates.append(update)
            self._condition.notify_all()
            return update

    async def _publish_chunk(self, data: bytes) -> None:
        if not data:
            return
        assert self._emulator is not None
        async with self._condition:
            # Snapshot publication uses this same lock. Feeding here makes the
            # emulator state and the assigned sequence one atomic ordering:
            # a concurrent keyframe can never include a chunk whose sequence
            # has not yet been appended to the stream.
            self._emulator.feed(data)
            self._sequence += 1
            self._updates.append(
                TerminalOutputUpdate(
                    sequence=self._sequence,
                    kind="chunk",
                    captured_at=datetime.now(timezone.utc),
                    data=data,
                )
            )
            self._condition.notify_all()

    async def _run_control_client(self) -> None:
        last_keyframe = asyncio.get_running_loop().time()
        try:
            self._process = await asyncio.create_subprocess_exec(
                "tmux",
                "-C",
                "attach-session",
                "-r",
                "-f",
                "ignore-size",
                "-t",
                self.session_name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert self._process.stdout is not None
            while True:
                try:
                    line = await asyncio.wait_for(
                        self._process.stdout.readline(), timeout=_KEYFRAME_INTERVAL_S
                    )
                except asyncio.TimeoutError:
                    await self._publish_keyframe()
                    last_keyframe = asyncio.get_running_loop().time()
                    continue
                if not line:
                    break
                if line.startswith(b"%output "):
                    payload = _control_output_bytes(line)
                    await self._publish_chunk(payload)
                elif line.startswith((b"%pane-died", b"%session-closed", b"%exit")):
                    break
                now = asyncio.get_running_loop().time()
                if now - last_keyframe >= _KEYFRAME_INTERVAL_S:
                    await self._publish_keyframe()
                    last_keyframe = now
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("tmux control output reader failed for %s", self.session_name)
        finally:
            self._closed = True
            if self._process is not None and self._process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._process.terminate()
                with contextlib.suppress(Exception):
                    await self._process.wait()
            async with self._condition:
                self._condition.notify_all()


class TerminalOutputRegistry:
    """Service-owned shared sources keyed by persisted session UUID."""

    def __init__(self, db: RepoDb) -> None:
        self._db = db
        self._outputs: dict[UUID, TmuxTerminalOutput] = {}
        self._lock = asyncio.Lock()

    async def open(self, session_id: UUID) -> TmuxTerminalOutput:
        async with self._lock:
            existing = self._outputs.get(session_id)
            if existing is not None and not existing.closed:
                return existing
            row = self._db.conn.execute(
                "SELECT transport, transport_ref FROM harness_sessions WHERE repository_id = ? AND session_id = ?",
                (self._db.repository_id, str(session_id)),
            ).fetchone()
            if row is None:
                raise ValueError(f"persisted session {session_id} does not exist")
            if str(row["transport"]) != "tmux":
                raise ValueError(f"session {session_id} does not expose a tmux terminal")
            output = TmuxTerminalOutput(str(row["transport_ref"]))
            await output.start()
            self._outputs[session_id] = output
            return output

    async def close(self) -> None:
        outputs = tuple(self._outputs.values())
        self._outputs.clear()
        for output in outputs:
            await output.close()


def _control_output_bytes(line: bytes) -> bytes:
    """Decode the byte-preserving tmux control-mode `%output` payload.

    Record format is ``%output %pane-id <tmux-escaped-output>\n``. Tmux uses
    C-style octal escapes for controls and doubles literal backslashes.
    """

    try:
        _marker, _pane, encoded = line.rstrip(b"\n").split(b" ", 2)
    except ValueError:
        return b""
    out = bytearray()
    i = 0
    while i < len(encoded):
        byte = encoded[i]
        if byte != 0x5C or i + 1 >= len(encoded):
            out.append(byte)
            i += 1
        elif encoded[i + 1] == 0x5C:
            out.append(0x5C)
            i += 2
        elif i + 3 < len(encoded) and all(48 <= part <= 55 for part in encoded[i + 1 : i + 4]):
            out.append(int(encoded[i + 1 : i + 4], 8))
            i += 4
        else:
            # Tmux has historically emitted a literal next byte for an
            # unrecognised escape. Preserve it rather than treating an output
            # corruption as a parser error.
            out.append(encoded[i + 1])
            i += 2
    return bytes(out)


def _feed_capture(emulator: VtEmulator, captured: str) -> None:
    """Place capture rows explicitly. capture-pane newlines are not VT CRLF."""

    for row, line in enumerate(captured.splitlines()):
        if row >= emulator.rows:
            break
        emulator.feed(f"\x1b[{row + 1};1H".encode("ascii"))
        emulator.feed(line.encode("utf-8", errors="replace"))


def _restore_tmux_screen_state(
    emulator: VtEmulator,
    pane: tmux.PaneTerminalState,
) -> None:
    """Apply read-only tmux metadata after the text-only bootstrap capture."""

    top = max(0, min(pane.rows - 1, pane.scroll_top))
    bottom = max(top, min(pane.rows - 1, pane.scroll_bottom))
    emulator.feed(f"\x1b[{top + 1};{bottom + 1}r".encode("ascii"))
    emulator.feed(b"\x1b[?1h" if pane.application_cursor else b"\x1b[?1l")
    emulator.feed(b"\x1b=" if pane.application_keypad else b"\x1b>")
    emulator.feed(b"\x1b[?7h" if pane.wraparound else b"\x1b[?7l")
    emulator.feed(b"\x1b[4h" if pane.insert else b"\x1b[4l")
    emulator.feed(b"\x1b[?25h" if pane.cursor_visible else b"\x1b[?25l")
    if pane.origin:
        emulator.feed(b"\x1b[?6h")
        cursor_row = pane.cursor_y - top + 1
    else:
        emulator.feed(b"\x1b[?6l")
        cursor_row = pane.cursor_y + 1
    cursor_row = max(1, min(bottom - top + 1 if pane.origin else pane.rows, cursor_row))
    cursor_column = max(1, min(pane.columns, pane.cursor_x + 1))
    emulator.feed(f"\x1b[{cursor_row};{cursor_column}H".encode("ascii"))


__all__ = [
    "TerminalOutputRegistry",
    "TerminalOutputUpdate",
    "TmuxTerminalOutput",
]
