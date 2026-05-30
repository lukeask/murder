#!/usr/bin/env python3
"""Run tmux_record under a real PTY; answers written to /tmp/claude-trust-record-answer.txt."""
from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ANSWER_FILE = Path("/tmp/claude-trust-record-answer.txt")
LOG = Path("/tmp/claude-trust-pty.log")
SESSION = "claude-trust-record"


def set_winsize(fd: int, *, rows: int = 40, cols: int = 120) -> None:
    winsz = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsz)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: claude_trust_pty_driver.py <label> <cwd> [--attach-only]", file=sys.stderr)
        return 2
    label = sys.argv[1]
    cwd = Path(sys.argv[2]).resolve()
    attach_only = "--attach-only" in sys.argv[3:]
    ANSWER_FILE.unlink(missing_ok=True)
    LOG.write_bytes(b"")

    master, slave = pty.openpty()
    set_winsize(slave)
    cmd = [
        sys.executable,
        str(REPO / "tools/testing/tmux_record.py"),
        "--session",
        SESSION,
        "--label",
        label,
        "--frame-interval",
        "0.1",
        "--cwd",
        str(cwd),
    ]
    if not attach_only:
        cmd.extend(["--", "claude", "--model", "haiku"])
    proc = subprocess.Popen(
        cmd,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=REPO,
        env={**os.environ, "TERM": "xterm-256color"},
    )
    os.close(slave)
    Path("/tmp/claude-trust-record.pid").write_text(str(proc.pid))

    buf = ""
    while True:
        if ANSWER_FILE.exists() and (
            "Save recording or discard" in buf or "Optional comment" in buf
        ):
            os.write(master, ANSWER_FILE.read_text(encoding="utf-8").encode())
            ANSWER_FILE.unlink(missing_ok=True)
        readable, _, _ = select.select([master], [], [], 0.25)
        if master in readable:
            try:
                data = os.read(master, 8192)
            except OSError:
                data = b""
            if not data:
                if proc.poll() is not None:
                    break
                continue
            LOG.write_bytes(data)
            chunk = data.decode(errors="replace")
            buf = (buf + chunk)[-4000:]
        elif proc.poll() is not None:
            break

    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
