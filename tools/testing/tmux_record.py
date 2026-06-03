#!/usr/bin/env python3
"""Interactive tmux recorder for manual harness capture sessions.

This wraps a real tmux client in a local PTY so the operator can use tmux
normally while the tool records:

- raw stdin bytes sent to tmux
- raw stdout bytes emitted by the tmux client
- periodic pane snapshots from tmux capture-pane

Artifacts are written under ``tools/testing/recordings/`` unless overridden.
"""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import json
import os
import pty
import select
import shlex
import signal
import struct
import subprocess
import sys
import termios
import threading
import tty
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

META_FIELD_COUNT = 10
ASCII_PRINTABLE_MIN = 32
ASCII_PRINTABLE_MAX = 126
CTRL_M = 13
CTRL_J = 10
CTRL_I = 9
ESC = 27
DEL = 127


class TmuxRecordError(RuntimeError):
    """Fatal recorder error."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def run_tmux(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        ["tmux", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "tmux failed").strip()
        raise TmuxRecordError(f"tmux {' '.join(shlex.quote(arg) for arg in args)} failed: {detail}")
    return cp


def session_exists(name: str) -> bool:
    return run_tmux(["has-session", "-t", name], check=False).returncode == 0


def make_session_name() -> str:
    return "tmux-record-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def terminal_size() -> os.terminal_size:
    try:
        return os.get_terminal_size(sys.stdin.fileno())
    except OSError:
        return os.terminal_size((120, 40))


def set_pty_size(fd: int, size: os.terminal_size) -> None:
    winsz = struct.pack("HHHH", size.lines, size.columns, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsz)


def create_session(name: str, cwd: Path, command: list[str], size: os.terminal_size) -> None:
    args = [
        "new-session",
        "-d",
        "-s",
        name,
        "-x",
        str(size.columns),
        "-y",
        str(size.lines),
        "-c",
        str(cwd),
    ]
    if command:
        args.extend(command)
    run_tmux(args)


def kill_session(name: str) -> None:
    run_tmux(["kill-session", "-t", name], check=False)


def capture_pane(session: str) -> dict[str, Any]:
    meta = run_tmux(
        [
            "display-message",
            "-p",
            "-t",
            session,
            "-F",
            "\t".join(
                [
                    "#{session_name}",
                    "#{window_index}",
                    "#{window_name}",
                    "#{pane_id}",
                    "#{pane_width}",
                    "#{pane_height}",
                    "#{cursor_x}",
                    "#{cursor_y}",
                    "#{pane_in_mode}",
                    "#{pane_current_command}",
                ]
            ),
        ]
    ).stdout.rstrip("\n")
    fields = meta.split("\t")
    if len(fields) != META_FIELD_COUNT:
        raise TmuxRecordError(f"unexpected tmux metadata payload: {meta!r}")
    pane = run_tmux(["capture-pane", "-p", "-t", session]).stdout
    return {
        "session_name": fields[0],
        "window_index": int(fields[1]),
        "window_name": fields[2],
        "pane_id": fields[3],
        "pane_width": int(fields[4]),
        "pane_height": int(fields[5]),
        "cursor_x": int(fields[6]),
        "cursor_y": int(fields[7]),
        "pane_in_mode": fields[8] == "1",
        "pane_current_command": fields[9],
        "text": pane,
    }


def slugify(value: str) -> str:
    out = []
    prev_dash = False
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
            continue
        if not prev_dash:
            out.append("-")
            prev_dash = True
    slug = "".join(out).strip("-")
    return slug or "recording"


def format_bytes(data: bytes, *, limit: int = 48) -> str:
    pieces: list[str] = []
    for byte in data[:limit]:
        if ASCII_PRINTABLE_MIN <= byte <= ASCII_PRINTABLE_MAX and chr(byte) not in {"\\", "'"}:
            pieces.append(chr(byte))
        elif byte == CTRL_M:
            pieces.append("<CR>")
        elif byte == CTRL_J:
            pieces.append("<LF>")
        elif byte == CTRL_I:
            pieces.append("<TAB>")
        elif byte == ESC:
            pieces.append("<ESC>")
        elif byte == DEL:
            pieces.append("<BS>")
        elif 0 <= byte < ASCII_PRINTABLE_MIN:
            pieces.append(f"<C-{chr(byte + 64)}>")
        else:
            pieces.append(f"\\x{byte:02x}")
    if len(data) > limit:
        pieces.append("...")
    return "".join(pieces)


def event_record(kind: str, when: datetime, started: datetime, data: bytes) -> dict[str, Any]:
    return {
        "kind": kind,
        "at": iso_timestamp(when),
        "t_rel_s": round((when - started).total_seconds(), 6),
        "byte_count": len(data),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "preview": format_bytes(data),
    }


def decode_process_status(status: int) -> dict[str, Any]:
    if status >= 0:
        return {"kind": "exit", "code": status}
    return {"kind": "signal", "signal": abs(status)}


@dataclass
class FrameSnapshot:
    at: str
    t_rel_s: float
    window_index: int
    window_name: str
    pane_id: str
    pane_width: int
    pane_height: int
    cursor_x: int
    cursor_y: int
    pane_in_mode: bool
    pane_current_command: str
    text: str

    @classmethod
    def from_capture(
        cls, capture: dict[str, Any], *, when: datetime, started: datetime
    ) -> FrameSnapshot:
        return cls(
            at=iso_timestamp(when),
            t_rel_s=round((when - started).total_seconds(), 6),
            window_index=capture["window_index"],
            window_name=capture["window_name"],
            pane_id=capture["pane_id"],
            pane_width=capture["pane_width"],
            pane_height=capture["pane_height"],
            cursor_x=capture["cursor_x"],
            cursor_y=capture["cursor_y"],
            pane_in_mode=capture["pane_in_mode"],
            pane_current_command=capture["pane_current_command"],
            text=capture["text"],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "t_rel_s": self.t_rel_s,
            "window_index": self.window_index,
            "window_name": self.window_name,
            "pane_id": self.pane_id,
            "pane_width": self.pane_width,
            "pane_height": self.pane_height,
            "cursor_x": self.cursor_x,
            "cursor_y": self.cursor_y,
            "pane_in_mode": self.pane_in_mode,
            "pane_current_command": self.pane_current_command,
            "text": self.text,
        }


class FrameSampler(threading.Thread):
    """Capture pane frames on an interval and on explicit wakeups."""

    def __init__(
        self,
        *,
        session: str,
        started: datetime,
        interval_s: float,
        stop_event: threading.Event,
        wake_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self._session = session
        self._recording_started = started
        self._interval_s = interval_s
        self._stop_event = stop_event
        self._wake_event = wake_event
        self.frames: list[FrameSnapshot] = []
        self.errors: list[str] = []
        self._last_signature: tuple[Any, ...] | None = None

    def run(self) -> None:
        self.capture_once(force=True)
        while not self._stop_event.is_set():
            woke = self._wake_event.wait(self._interval_s)
            self._wake_event.clear()
            self.capture_once(force=woke)
        self.capture_once(force=True)

    def capture_once(self, *, force: bool) -> None:
        try:
            raw = capture_pane(self._session)
        except Exception as exc:  # pragma: no cover - manual tooling path
            self.errors.append(str(exc))
            return
        signature = (
            raw["window_index"],
            raw["window_name"],
            raw["pane_width"],
            raw["pane_height"],
            raw["cursor_x"],
            raw["cursor_y"],
            raw["pane_in_mode"],
            raw["pane_current_command"],
            raw["text"],
        )
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature
        self.frames.append(
            FrameSnapshot.from_capture(raw, when=utc_now(), started=self._recording_started)
        )


class PtyPassthrough:
    """Drive a child tmux client through a local PTY while logging traffic."""

    def __init__(self, session: str, started: datetime) -> None:
        self.session = session
        self.started = started
        self.events: list[dict[str, Any]] = []
        self.child_status: int | None = None

    def run(self, wake_frames: threading.Event) -> int:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise TmuxRecordError("tmux_record.py must run in an interactive terminal")

        parent_fd, child_fd = pty.openpty()
        size = terminal_size()
        set_pty_size(parent_fd, size)
        old_attrs = termios.tcgetattr(sys.stdin.fileno())
        child = subprocess.Popen(  # noqa: S603
            ["tmux", "attach-session", "-t", self.session],
            stdin=child_fd,
            stdout=child_fd,
            stderr=child_fd,
            close_fds=True,
        )
        os.close(child_fd)
        child_fd = -1

        def on_sigwinch(signum: int, frame: object) -> None:
            del signum, frame
            new_size = terminal_size()
            set_pty_size(parent_fd, new_size)

        old_winch = signal.getsignal(signal.SIGWINCH)
        signal.signal(signal.SIGWINCH, on_sigwinch)
        tty.setraw(sys.stdin.fileno())
        try:
            while True:
                readable, _, _ = select.select([sys.stdin.fileno(), parent_fd], [], [])
                if sys.stdin.fileno() in readable:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if not data:
                        os.close(parent_fd)
                        break
                    self.events.append(event_record("stdin", utc_now(), self.started, data))
                    os.write(parent_fd, data)
                    wake_frames.set()
                if parent_fd in readable:
                    try:
                        data = os.read(parent_fd, 4096)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        data = b""
                    if not data:
                        break
                    self.events.append(event_record("stdout", utc_now(), self.started, data))
                    os.write(sys.stdout.fileno(), data)
            status = child.wait()
            self.child_status = status
            return status
        finally:
            if child_fd >= 0:
                os.close(child_fd)
            os.close(parent_fd)
            signal.signal(signal.SIGWINCH, old_winch)
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attrs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record raw terminal traffic and pane snapshots while using tmux normally."
    )
    parser.add_argument("--session", help="Attach to this tmux session; create it if missing.")
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for a new session (default: current directory).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tools/testing/recordings"),
        help="Directory where saved recordings are written.",
    )
    parser.add_argument(
        "--frame-interval",
        type=float,
        default=0.05,
        help="Seconds between frame snapshots while idle (default: 0.05, 20 Hz).",
    )
    parser.add_argument(
        "--keep-session",
        action="store_true",
        help="Do not kill a newly created tmux session after recording ends.",
    )
    parser.add_argument(
        "--label",
        help="Optional label used in the saved recording directory name.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Optional command for a new tmux session. Use `-- <cmd ...>`.",
    )
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args


def prompt_choice() -> str:
    while True:
        choice = input("Save recording or discard it? [s/d]: ").strip().lower()
        if choice in {"s", "save"}:
            return "save"
        if choice in {"d", "discard"}:
            return "discard"
        print("Enter `s` to save or `d` to discard.")


def prompt_comment() -> str:
    print("Optional comment. Finish with an empty line:")
    lines: list[str] = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def save_recording(
    *,
    output_dir: Path,
    label: str | None,
    session: str,
    created_session: bool,
    command: list[str],
    cwd: Path,
    started: datetime,
    ended: datetime,
    status: int,
    comment: str,
    events: list[dict[str, Any]],
    frames: list[FrameSnapshot],
    frame_errors: list[str],
) -> Path:
    slug_parts = [started.strftime("%Y%m%d-%H%M%S")]
    if label:
        slug_parts.append(slugify(label))
    recording_dir = output_dir / "-".join(slug_parts)
    recording_dir.mkdir(parents=True, exist_ok=False)

    metadata = {
        "session": session,
        "created_session": created_session,
        "cwd": str(cwd),
        "command": command,
        "command_shell": shell_join(command) if command else None,
        "started_at": iso_timestamp(started),
        "ended_at": iso_timestamp(ended),
        "duration_s": round((ended - started).total_seconds(), 3),
        "attach_exit_status": status,
        "attach_result": decode_process_status(status),
        "comment": comment,
        "event_count": len(events),
        "frame_count": len(frames),
        "frame_errors": frame_errors,
    }
    write_json(recording_dir / "metadata.json", metadata)
    write_jsonl(recording_dir / "events.jsonl", events)
    write_jsonl(recording_dir / "frames.jsonl", [frame.to_json() for frame in frames])
    return recording_dir


def main() -> int:
    args = parse_args()
    session = args.session or make_session_name()
    started = utc_now()
    cwd = args.cwd.resolve()
    output_dir = args.output_dir.resolve()
    command = list(args.command)
    created_session = False

    exists = session_exists(session)
    if exists and command:
        raise TmuxRecordError(
            "cannot pass `-- <command>` when attaching to an existing tmux session"
        )
    if not exists:
        create_session(session, cwd, command, terminal_size())
        created_session = True

    print(
        f"Recording tmux session `{session}`. "
        f"Detach with your usual tmux binding or exit the shell to stop.",
        file=sys.stderr,
    )

    stop_frames = threading.Event()
    wake_frames = threading.Event()
    sampler = FrameSampler(
        session=session,
        started=started,
        interval_s=max(0.05, float(args.frame_interval)),
        stop_event=stop_frames,
        wake_event=wake_frames,
    )
    sampler.start()

    status: int = 0
    passthrough = PtyPassthrough(session, started)
    try:
        status = passthrough.run(wake_frames)
    finally:
        stop_frames.set()
        sampler.join(timeout=2.0)

    ended = utc_now()
    print("", file=sys.stderr)
    choice = prompt_choice()
    if choice == "discard":
        if created_session and not args.keep_session:
            kill_session(session)
        print("Recording discarded.", file=sys.stderr)
        return 0

    comment = prompt_comment()
    saved_to = save_recording(
        output_dir=output_dir,
        label=args.label,
        session=session,
        created_session=created_session,
        command=command,
        cwd=cwd,
        started=started,
        ended=ended,
        status=status,
        comment=comment,
        events=passthrough.events,
        frames=sampler.frames,
        frame_errors=sampler.errors,
    )
    if created_session and not args.keep_session:
        kill_session(session)
    print(f"Saved recording to {saved_to}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TmuxRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
