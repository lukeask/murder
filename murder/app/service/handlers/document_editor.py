"""Interactive document editor application handlers."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.document_editor import (
    DocumentEditorTargetParams,
    ResizeDocumentEditorParams,
    StartDocumentEditorParams,
)
from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.document_editors import (
    DocumentEditorService,
    EditorDisposition,
    StartDocumentEditor,
    TerminalSize,
    document_target,
)


def register(app: ApplicationRegistrar, editors: DocumentEditorService) -> None:
    async def start(params: dict[str, Any]) -> dict[str, object]:
        parsed = StartDocumentEditorParams.model_validate(params)
        result = await editors.start(
            StartDocumentEditor(
                target=document_target(parsed.kind, parsed.name),
                size=TerminalSize(columns=parsed.columns, rows=parsed.rows),
            )
        )
        return {
            "status": "active",
            "document_path": str(result.document_path),
            "terminal_session_id": str(result.session_id),
            "reused": result.disposition is EditorDisposition.REUSED,
        }

    async def send_input(params: dict[str, Any]) -> dict[str, object]:
        # Kept as a closed-capability compatibility shape until clients that
        # know about it have upgraded, but never let it bypass the fenced raw
        # terminal stream.  ``terminal.input`` is the sole editor write path.
        del params
        raise RuntimeError("use terminal.input instead of document.editor.input")

    async def resize(params: dict[str, Any]) -> dict[str, object]:
        parsed = ResizeDocumentEditorParams.model_validate(params)
        await editors.resize(
            parsed.terminal_session_id,
            TerminalSize(columns=parsed.columns, rows=parsed.rows),
        )
        return {"handled": True}

    async def status(params: dict[str, Any]) -> dict[str, object]:
        parsed = DocumentEditorTargetParams.model_validate(params)
        result = await editors.status(parsed.terminal_session_id)
        return {
            "status": "active" if result.active else "exited",
            "document_path": str(result.document_path),
            "terminal_session_id": str(result.session_id),
        }

    app.register_application_command(CommandName.DOCUMENT_EDITOR_START, start)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_INPUT, send_input)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_RESIZE, resize)
    app.register_application_command(CommandName.DOCUMENT_EDITOR_STATUS, status)


__all__ = ["register"]
