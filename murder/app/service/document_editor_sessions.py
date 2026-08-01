"""Service-owned interactive editor tmux sessions for Murder documents."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from murder.app.service.document_access import DocumentAccess
from murder.app.service.terminal_capture import CapturedTerminalFrame
from murder.runtime.terminal import tmux


@dataclass(frozen=True)
class EditorSession:
    session_id: UUID
    document_path: Path
    tmux_name: str


class DocumentEditorSessions:
    """Resolve, validate, create, reuse and drive one editor session per document."""

    def __init__(self, repo_root: Path, documents: DocumentAccess) -> None:
        self.repo_root = repo_root.resolve()
        self.documents = documents
        self._by_id: dict[UUID, EditorSession] = {}
        self._by_path: dict[Path, EditorSession] = {}
        self._start_lock = asyncio.Lock()

    def update_documents(self, documents: DocumentAccess) -> None:
        self.documents = documents

    def _resolve(self, kind: str, name: str) -> Path:
        if name != name.strip() or Path(name).name != name or "\\" in name or name in {".", ".."}:
            raise ValueError("document name must be a single safe path component")
        if kind == "plan":
            path = self.documents.plan_path_for(name)
        elif kind == "note":
            path = self.documents.note_path_for(name)
        elif kind == "report":
            path = self.documents.report_path_for(name)
        else:
            raise ValueError(f"unsupported document kind: {kind}")
        canonical = path.resolve(strict=False)
        if not canonical.is_relative_to(self.repo_root):
            raise ValueError("document path is outside the repository")
        if canonical.suffix.lower() != ".md":
            raise ValueError("Murder documents must be markdown files")
        return canonical

    def _identity(self, path: Path) -> EditorSession:
        existing = self._by_path.get(path)
        if existing is not None:
            return existing
        identity = uuid5(NAMESPACE_URL, f"murder-editor:{self.repo_root}:{path}")
        project = hashlib.sha256(str(self.repo_root).encode()).hexdigest()[:10]
        document = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        session = EditorSession(identity, path, f"murder_editor_{project}_{document}")
        self._by_path[path] = session
        self._by_id[identity] = session
        return session

    def _editor_argv(self) -> list[str]:
        visual = os.environ.get("VISUAL")
        configured = visual if visual is not None and visual.strip() else os.environ.get("EDITOR")
        if configured is None or not configured.strip():
            raise RuntimeError("no editor configured. Set $VISUAL or $EDITOR.")
        try:
            editor_argv = shlex.split(configured)
        except ValueError as exc:
            raise RuntimeError(f"invalid editor configuration: {exc}") from exc
        if not editor_argv:
            raise RuntimeError("no editor configured. Set $VISUAL or $EDITOR.")

        executable = editor_argv[0]
        if "/" in executable:
            candidate = Path(executable)
            if not candidate.is_absolute():
                candidate = self.repo_root / candidate
            launchable = candidate.is_file() and os.access(candidate, os.X_OK)
        else:
            launchable = shutil.which(executable) is not None
        if not launchable:
            raise RuntimeError(f"configured editor executable not found: {executable}")
        return editor_argv

    async def start(
        self, kind: str, name: str, *, columns: int, rows: int
    ) -> tuple[EditorSession, bool]:
        path = self._resolve(kind, name)
        session = self._identity(path)
        async with self._start_lock:
            if await tmux.session_exists(session.tmux_name):
                await tmux.resize_session(session.tmux_name, columns=columns, rows=rows)
                return session, True
            editor_argv = self._editor_argv()
            path.parent.mkdir(parents=True, exist_ok=True)
            await tmux.create_session(
                session.tmux_name,
                self.repo_root,
                [*editor_argv, str(path)],
                width=columns,
                height=rows,
            )
            return session, False

    def get(self, session_id: UUID) -> EditorSession:
        try:
            return self._by_id[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown document editor session {session_id}") from exc

    async def active(self, session_id: UUID) -> bool:
        return await tmux.session_exists(self.get(session_id).tmux_name)

    async def send(self, session_id: UUID, key: str, *, literal: bool) -> None:
        session = self.get(session_id)
        if not await tmux.session_exists(session.tmux_name):
            raise RuntimeError("document editor has exited")
        await tmux.send_keys(session.tmux_name, key, literal=literal, enter=False)

    async def resize(self, session_id: UUID, *, columns: int, rows: int) -> None:
        session = self.get(session_id)
        if await tmux.session_exists(session.tmux_name):
            await tmux.resize_session(session.tmux_name, columns=columns, rows=rows)

    async def capture(self, session_id: UUID) -> CapturedTerminalFrame | None:
        session = self._by_id.get(session_id)
        if session is None:
            return None
        if not await tmux.session_exists(session.tmux_name):
            raise RuntimeError("document editor has exited")
        data = await tmux.capture_viewport(session.tmux_name, escapes=True)
        columns, rows = await tmux.pane_dimensions(session.tmux_name)
        return CapturedTerminalFrame(data=data, columns=columns, rows=rows)


__all__ = ["DocumentEditorSessions", "EditorSession"]
