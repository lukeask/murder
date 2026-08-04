"""Service-owned interactive editor tmux sessions for Murder documents."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from murder.app.service.documents import (
    DocumentService,
    DocumentTarget,
    NoteDocument,
    PlanDocument,
    ReportDocument,
)
from murder.runtime.sessions.contracts import SessionCapabilities
from murder.runtime.sessions.service import (
    SessionBackendKind,
    SessionService,
    TmuxSessionRegistration,
)
from murder.runtime.terminal import tmux


@dataclass(frozen=True)
class TerminalSize:
    columns: int
    rows: int


@dataclass(frozen=True)
class StartDocumentEditor:
    target: DocumentTarget
    size: TerminalSize


class EditorDisposition(str, Enum):
    CREATED = "created"
    REUSED = "reused"


@dataclass(frozen=True)
class EditorStartResult:
    session_id: UUID
    document_path: Path
    disposition: EditorDisposition
    tmux_name: str


@dataclass(frozen=True)
class EditorStatus:
    session_id: UUID
    document_path: Path
    active: bool


@dataclass(frozen=True)
class EditorSession:
    session_id: UUID
    document_path: Path
    tmux_name: str


class DocumentEditorService:
    """Resolve, validate, create, reuse and drive one editor session per document."""

    def __init__(
        self,
        repo_root: Path,
        documents: DocumentService,
        *,
        sessions: SessionService,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.documents = documents
        self._sessions = sessions
        self._by_id: dict[UUID, EditorSession] = {}
        self._by_path: dict[Path, EditorSession] = {}
        self._start_lock = asyncio.Lock()

    def _resolve(self, target: DocumentTarget) -> Path:
        name = target.name
        if name != name.strip() or Path(name).name != name or "\\" in name or name in {".", ".."}:
            raise ValueError("document name must be a single safe path component")
        path = self.documents.path_for(target)
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

    async def _register_session(self, session: EditorSession) -> None:
        await self._sessions.ensure_persisted_tmux_session(
            TmuxSessionRegistration(
                session_id=session.session_id,
                session_kind="document_editor",
                tmux_name=session.tmux_name,
                capabilities=SessionCapabilities(raw_terminal=True, interruptible=False),
                backend=SessionBackendKind.PLAIN_TMUX,
            )
        )

    async def start(self, request: StartDocumentEditor) -> EditorStartResult:
        path = self._resolve(request.target)
        async with self._start_lock:
            # Identity + tmux create/reuse share the lock so concurrent starts
            # for one document converge on a single process-local session (§6.15).
            session = self._identity(path)
            if await tmux.session_exists(session.tmux_name):
                await tmux.resize_session(
                    session.tmux_name,
                    columns=request.size.columns,
                    rows=request.size.rows,
                )
                await self._register_session(session)
                disposition = EditorDisposition.REUSED
            else:
                editor_argv = self._editor_argv()
                path.parent.mkdir(parents=True, exist_ok=True)
                await tmux.create_session(
                    session.tmux_name,
                    self.repo_root,
                    [*editor_argv, str(path)],
                    width=request.size.columns,
                    height=request.size.rows,
                )
                await self._register_session(session)
                disposition = EditorDisposition.CREATED
            return EditorStartResult(
                session_id=session.session_id,
                document_path=session.document_path,
                disposition=disposition,
                tmux_name=session.tmux_name,
            )

    def get(self, session_id: UUID) -> EditorSession:
        try:
            return self._by_id[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown document editor session {session_id}") from exc

    async def status(self, session_id: UUID) -> EditorStatus:
        session = self.get(session_id)
        return EditorStatus(
            session_id=session.session_id,
            document_path=session.document_path,
            active=await tmux.session_exists(session.tmux_name),
        )

    async def resize(self, session_id: UUID, size: TerminalSize) -> None:
        session = self.get(session_id)
        if await tmux.session_exists(session.tmux_name):
            await tmux.resize_session(
                session.tmux_name, columns=size.columns, rows=size.rows
            )


def document_target(kind: str, name: str) -> DocumentTarget:
    """Map protocol kind strings onto typed document targets."""
    match kind:
        case "plan":
            return PlanDocument(name)
        case "note":
            return NoteDocument(name)
        case "report":
            return ReportDocument(name)
        case _:
            raise ValueError(f"unsupported document kind: {kind}")


__all__ = [
    "DocumentEditorService",
    "EditorDisposition",
    "EditorSession",
    "EditorStartResult",
    "EditorStatus",
    "StartDocumentEditor",
    "TerminalSize",
    "document_target",
]
