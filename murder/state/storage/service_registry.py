"""Per-user registry for running murder service instances."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSION_HASH_LEN = 12
RUNTIME_SUBDIR = "murder"


@dataclass(frozen=True)
class ServiceSession:
    name: str
    basename: str
    path_hash: str
    repo_root: Path
    pid: int
    websocket_url: str


class AmbiguousServiceSessionError(ValueError):
    def __init__(self, selector: str, matches: list[ServiceSession]) -> None:
        super().__init__(selector)
        self.selector = selector
        self.matches = matches


def service_runtime_root() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / RUNTIME_SUBDIR
    return Path(f"/tmp/murder-{os.getuid()}")


def project_path_hash(repo_root: Path) -> str:
    resolved = str(repo_root.resolve(strict=False))
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:SESSION_HASH_LEN]


def project_session_basename(repo_root: Path) -> str:
    raw = repo_root.resolve(strict=False).name or "root"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return cleaned or "root"


def project_session_name(repo_root: Path) -> str:
    return f"{project_session_basename(repo_root)}-{project_path_hash(repo_root)}"


def service_registry_dir() -> Path:
    return service_runtime_root() / "sessions"


def service_registry_path(name: str) -> Path:
    return service_registry_dir() / f"{name}.json"


def write_service_session(
    repo_root: Path,
    websocket_url: str,
    *,
    pid: int | None = None,
) -> ServiceSession:
    repo_root = repo_root.resolve(strict=False)
    session = ServiceSession(
        name=project_session_name(repo_root),
        basename=project_session_basename(repo_root),
        path_hash=project_path_hash(repo_root),
        repo_root=repo_root,
        pid=pid or os.getpid(),
        websocket_url=websocket_url,
    )
    path = service_registry_path(session.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(
            {
                "name": session.name,
                "basename": session.basename,
                "path_hash": session.path_hash,
                "repo_root": str(session.repo_root),
                "pid": session.pid,
                "websocket_url": session.websocket_url,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return session


def remove_service_session(name: str) -> None:
    try:
        service_registry_path(name).unlink()
    except FileNotFoundError:
        pass


def read_service_session(path: Path) -> ServiceSession | None:
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return ServiceSession(
            name=str(raw["name"]),
            basename=str(raw["basename"]),
            path_hash=str(raw["path_hash"]),
            repo_root=Path(str(raw["repo_root"])),
            pid=int(raw["pid"]),
            websocket_url=str(raw["websocket_url"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def list_service_sessions() -> list[ServiceSession]:
    root = service_registry_dir()
    if not root.exists():
        return []
    sessions: list[ServiceSession] = []
    for path in sorted(root.glob("*.json")):
        session = read_service_session(path)
        if session is not None:
            sessions.append(session)
    return sessions


def resolve_service_session_selector(
    selector: str,
    sessions: list[ServiceSession],
) -> ServiceSession | None:
    exact = [session for session in sessions if session.name == selector]
    if exact:
        return exact[0]

    basename_matches = [session for session in sessions if session.basename == selector]
    if len(basename_matches) > 1:
        raise AmbiguousServiceSessionError(selector, basename_matches)
    if basename_matches:
        return basename_matches[0]
    return None


__all__ = [
    "AmbiguousServiceSessionError",
    "ServiceSession",
    "list_service_sessions",
    "project_path_hash",
    "project_session_basename",
    "project_session_name",
    "remove_service_session",
    "resolve_service_session_selector",
    "service_registry_dir",
    "service_registry_path",
    "service_runtime_root",
    "write_service_session",
]
