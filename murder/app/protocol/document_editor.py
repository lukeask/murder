"""Interactive document-editor application contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from murder.app.protocol.common import ApplicationModel


class StartDocumentEditorParams(ApplicationModel):
    kind: Literal["plan", "note", "report"]
    name: str = Field(min_length=1)
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if value != value.strip() or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("document name must be a single safe path component")
        return value


class DocumentEditorSessionResult(ApplicationModel):
    status: Literal["active", "exited"]
    document_path: str
    terminal_session_id: UUID
    reused: bool = False


class DocumentEditorTargetParams(ApplicationModel):
    terminal_session_id: UUID


class DocumentEditorInputParams(DocumentEditorTargetParams):
    key: str
    literal: bool = True


class ResizeDocumentEditorParams(DocumentEditorTargetParams):
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)


class DocumentEditorStatusResult(ApplicationModel):
    status: Literal["active", "exited"]
    document_path: str
    terminal_session_id: UUID


class DocumentEditorMutationResult(ApplicationModel):
    handled: Literal[True] = True


__all__ = [
    "DocumentEditorInputParams",
    "DocumentEditorMutationResult",
    "DocumentEditorSessionResult",
    "DocumentEditorStatusResult",
    "DocumentEditorTargetParams",
    "ResizeDocumentEditorParams",
    "StartDocumentEditorParams",
]
