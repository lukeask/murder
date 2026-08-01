"""Interactive document editor application handlers."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.document_editor_sessions import EditorSession


class DocumentEditorEffects(Protocol):
    async def start_document_editor(
        self, kind: str, name: str, columns: int, rows: int
    ) -> tuple[EditorSession, bool]: ...

    async def resize_document_editor(self, session_id: UUID, columns: int, rows: int) -> None: ...

    async def document_editor_status(self, session_id: UUID) -> tuple[EditorSession, bool]: ...


def register(app: ApplicationRegistrar, effects: DocumentEditorEffects) -> None:
    async def start(params: dict[str, Any]) -> dict[str, object]:
        session, reused = await effects.start_document_editor(
            str(params["kind"]), str(params["name"]), int(params["columns"]), int(params["rows"])
        )
        return {
            "status": "active",
            "document_path": str(session.document_path),
            "terminal_session_id": str(session.session_id),
            "reused": reused,
        }

    async def send_input(params: dict[str, Any]) -> dict[str, object]:
        # Kept as a closed-capability compatibility shape until clients that
        # know about it have upgraded, but never let it bypass the fenced raw
        # terminal stream.  ``terminal.input`` is the sole editor write path.
        del params
        raise RuntimeError("use terminal.input instead of document.editor.input")

    async def resize(params: dict[str, Any]) -> dict[str, object]:
        await effects.resize_document_editor(
            UUID(str(params["terminal_session_id"])), int(params["columns"]), int(params["rows"])
        )
        return {"handled": True}

    async def status(params: dict[str, Any]) -> dict[str, object]:
        session, active = await effects.document_editor_status(
            UUID(str(params["terminal_session_id"]))
        )
        return {
            "status": "active" if active else "exited",
            "document_path": str(session.document_path),
            "terminal_session_id": str(session.session_id),
        }

    app.register_application_command(CommandName.DOCUMENT_EDITOR_START, start)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_INPUT, send_input)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_RESIZE, resize)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_STATUS, status)


__all__ = ["DocumentEditorEffects", "register"]
