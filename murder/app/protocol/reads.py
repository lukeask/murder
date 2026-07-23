"""Read-only listing and diagnostic application contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from murder.app.protocol.common import ApplicationModel
from murder.app.protocol.read_models import (
    ConversationsSnapshot,
    HistorySnapshot,
    NoteDisplaySnapshot,
    NotesSnapshot,
    PlanDisplaySnapshot,
    PlansSnapshot,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ScheduleSnapshot,
    TicketDetailSnapshot,
)
from murder.state.storage.git_transit import TransitSnapshot


class EmptyParams(ApplicationModel):
    """An operation which accepts no caller supplied fields."""


class TicketGetParams(ApplicationModel):
    ticket_id: str = Field(min_length=1)

    @field_validator("ticket_id")
    @classmethod
    def strip_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("ticket_id must be non-empty")
        return text


class NamedReadParams(ApplicationModel):
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("name must be non-empty")
        return text


class CommandGetParams(ApplicationModel):
    command_id: str = Field(min_length=1)

    @field_validator("command_id")
    @classmethod
    def strip_command_id(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("command_id must be non-empty")
        return text


class HealthGetResult(ApplicationModel):
    ok: Literal[True] = True
    run_id: str | None = None
    pid: int


class CommandGetResult(ApplicationModel):
    ok: bool
    error: Literal["runtime_db_unavailable", "not_found"] | None = None
    command_id: str | None = None
    status: str | None = None
    result_json: str | None = None
    last_error: str | None = None
    updated_at: str | None = None


class ConversationsGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: ConversationsSnapshot


class ScheduleGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: ScheduleSnapshot


class PlansListResult(ApplicationModel):
    ok: Literal[True] = True
    value: PlansSnapshot


class NotesListResult(ApplicationModel):
    ok: Literal[True] = True
    value: NotesSnapshot


class ReportsListResult(ApplicationModel):
    ok: Literal[True] = True
    value: ReportsSnapshot


class HistoryListResult(ApplicationModel):
    ok: Literal[True] = True
    value: HistorySnapshot


class TransitGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: TransitSnapshot


class TicketGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: TicketDetailSnapshot | None


class PlanGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: PlanDisplaySnapshot | None


class NoteGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: NoteDisplaySnapshot | None


class ReportGetResult(ApplicationModel):
    ok: Literal[True] = True
    value: ReportDisplaySnapshot | None


class HarnessModelEntry(ApplicationModel):
    id: str
    label: str


class HarnessModelsSnapshot(ApplicationModel):
    models: dict[str, list[HarnessModelEntry]] = Field(default_factory=dict)
    as_of: str | None = None


class HarnessModelsListResult(ApplicationModel):
    ok: Literal[True] = True
    value: HarnessModelsSnapshot


class WorktreeEntry(ApplicationModel):
    path: str
    branch: str | None = None
    is_main: bool


class WorktreesListResult(ApplicationModel):
    ok: Literal[True] = True
    entries: list[WorktreeEntry]


__all__ = [
    "CommandGetParams",
    "CommandGetResult",
    "ConversationsGetResult",
    "ConversationsSnapshot",
    "EmptyParams",
    "HarnessModelEntry",
    "HarnessModelsListResult",
    "HarnessModelsSnapshot",
    "HealthGetResult",
    "HistoryListResult",
    "HistorySnapshot",
    "NamedReadParams",
    "NoteDisplaySnapshot",
    "NoteGetResult",
    "NotesListResult",
    "NotesSnapshot",
    "PlanDisplaySnapshot",
    "PlanGetResult",
    "PlansListResult",
    "PlansSnapshot",
    "ReportDisplaySnapshot",
    "ReportGetResult",
    "ReportsListResult",
    "ReportsSnapshot",
    "ScheduleGetResult",
    "ScheduleSnapshot",
    "TicketDetailSnapshot",
    "TicketGetParams",
    "TicketGetResult",
    "TransitGetResult",
    "TransitSnapshot",
    "WorktreeEntry",
    "WorktreesListResult",
]
