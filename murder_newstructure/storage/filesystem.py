"""Atomic-write helpers + flock convenience.

All state-bearing markdown files use atomic writes (tempfile + os.replace)
so a SIGKILL mid-write doesn't leave a half-truncated file.
"""

from __future__ import annotations

import errno
import fcntl
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically.

    Tempfile in the same directory (so os.replace is on one filesystem),
    flush + fsync before replace (so a crash post-rename can't yield a
    zero-length file).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup; don't mask the real error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def acquire_flock(path: Path) -> int:
    """Open `path`, take an exclusive non-blocking flock, return the fd.

    Caller stores fd to keep the lock alive (closing the fd releases the
    lock). Raises BlockingIOError if held by another process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EACCES, errno.EAGAIN):
            raise BlockingIOError(f"lock held: {path}") from e
        raise
    # Record our pid so `murder up` can identify the live owner.
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def release_flock(fd: int) -> None:
    """Release a flock acquired via `acquire_flock` and close the fd."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read_lock_pid(path: Path) -> int | None:
    """Return the pid recorded in a lockfile, or None if unreadable."""
    try:
        with open(path, encoding="ascii") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None
