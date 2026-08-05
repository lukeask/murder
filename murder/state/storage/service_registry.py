"""Per-user registry for the single murder daemon process.

One ``daemon.json`` record (pid, port, started_at) under the runtime root.
CLI uses it for staleness checks and ``murder down``; readiness is the live
listener on ``127.0.0.1:62077`` plus the daemon flock.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SESSION_HASH_LEN = 12
RUNTIME_SUBDIR = "murder"
DAEMON_REGISTRY_NAME = "daemon.json"


@dataclass(frozen=True)
class DaemonRecord:
    """Published identity of the running murder daemon."""

    pid: int
    port: int
    started_at: str


def _xdg_runtime_dir() -> str | None:
    """Return XDG_RUNTIME_DIR when set to a non-empty path.

    An empty or whitespace-only value (common in stripped MCP / container envs)
    is treated as unset so clients do not use ``Path("")`` as a runtime root.
    """
    value = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    return value or None


def _default_user_runtime_dir() -> Path | None:
    """Return ``/run/user/<uid>`` when it is a usable directory, else None."""
    path = Path(f"/run/user/{os.getuid()}")
    try:
        if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
            return path
    except OSError:
        return None
    return None


def service_runtime_root() -> Path:
    """Per-user directory for the live daemon registry.

    Preference order:
    1. Non-empty ``XDG_RUNTIME_DIR`` (tests and explicit overrides).
    2. ``/run/user/<uid>/murder`` when that parent exists — so a client with
       empty/unset ``XDG_RUNTIME_DIR`` still shares the registry with a daemon
       started from a normal login shell.
    3. ``/tmp/murder-<uid>`` as a last resort.
    """
    runtime_dir = _xdg_runtime_dir()
    if runtime_dir is not None:
        return Path(runtime_dir) / RUNTIME_SUBDIR
    user_runtime = _default_user_runtime_dir()
    if user_runtime is not None:
        return user_runtime / RUNTIME_SUBDIR
    return Path(f"/tmp/murder-{os.getuid()}")


def project_path_hash(repo_root: Path) -> str:
    resolved = str(repo_root.resolve(strict=False))
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:SESSION_HASH_LEN]


def project_session_basename(repo_root: Path) -> str:
    raw = repo_root.resolve(strict=False).name or "root"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return cleaned or "root"


def project_session_name(repo_root: Path) -> str:
    """Stable path-derived label (basename + hash). Used by ``murder id``."""
    return f"{project_session_basename(repo_root)}-{project_path_hash(repo_root)}"


def daemon_registry_path() -> Path:
    return service_runtime_root() / DAEMON_REGISTRY_NAME


def write_daemon_record(
    *,
    port: int,
    pid: int | None = None,
    started_at: str | None = None,
) -> DaemonRecord:
    """Atomically publish the live daemon record under the runtime root."""
    record = DaemonRecord(
        pid=pid or os.getpid(),
        port=int(port),
        started_at=started_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    path = daemon_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "port": record.port,
                "started_at": record.started_at,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return record


def remove_daemon_record() -> None:
    try:
        daemon_registry_path().unlink()
    except FileNotFoundError:
        pass


def read_daemon_record() -> DaemonRecord | None:
    path = daemon_registry_path()
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return DaemonRecord(
            pid=int(raw["pid"]),
            port=int(raw["port"]),
            started_at=str(raw["started_at"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def live_daemon_record() -> DaemonRecord | None:
    """Return the registry record when its pid is still alive; else clear stale."""
    record = read_daemon_record()
    if record is None:
        return None
    try:
        os.kill(record.pid, 0)
    except OSError:
        remove_daemon_record()
        return None
    return record


__all__ = [
    "DAEMON_REGISTRY_NAME",
    "DaemonRecord",
    "daemon_registry_path",
    "live_daemon_record",
    "project_path_hash",
    "project_session_basename",
    "project_session_name",
    "read_daemon_record",
    "remove_daemon_record",
    "service_runtime_root",
    "write_daemon_record",
]
