"""Typed workflow definition, inspection, and signaling application contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator

from murder.app.protocol.common import ApplicationModel
from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.runtime import (
    ExternalWorkflowSignal,
    WorkflowRunRecord,
    WorkflowSignalRecord,
    WorkflowStatus,
    WorkflowWaitRecord,
)


class GetWorkflowsParams(ApplicationModel):
    """Empty params object for ``workflows.get``."""


class GetWorkflowsResult(ApplicationModel):
    ok: Literal[True] = True
    workflows: list[WorkflowDef]


class SetWorkflowsParams(ApplicationModel):
    workflows: list[WorkflowDef]


class SetWorkflowsResult(ApplicationModel):
    ok: Literal[True] = True
    workflows: list[WorkflowDef]


class StartWorkflowParams(ApplicationModel):
    name: str = Field(min_length=1)
    args: dict[str, str] = Field(default_factory=dict)
    request_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("name must be non-empty")
        return text

    @field_validator("args", mode="before")
    @classmethod
    def coerce_args(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("args must be an object")
        return {str(key): str(item) for key, item in value.items()}


class StartWorkflowResult(ApplicationModel):
    ok: Literal[True] = True
    run_ticket_id: str
    stage_ticket_ids: dict[str, str]
    created_ticket_ids: list[str]


class ListWorkflowRunsParams(ApplicationModel):
    status: WorkflowStatus | None = None
    definition_name: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ListWorkflowRunsResult(ApplicationModel):
    runs: list[WorkflowRunRecord]


class GetWorkflowRunParams(ApplicationModel):
    workflow_id: UUID
    include_waits: bool = True


class GetWorkflowRunResult(ApplicationModel):
    ok: bool
    run: WorkflowRunRecord | None = None
    waits: list[WorkflowWaitRecord] = Field(default_factory=list)
    error: Literal["not_found"] | None = None


class SignalWorkflowParams(ApplicationModel):
    """Enqueue an external workflow signal and wake the run."""

    workflow_id: UUID
    name: str = Field(min_length=1)
    correlation_key: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    deduplication_key: str | None = None
    request_id: UUID | None = None

    def external_signal(self) -> ExternalWorkflowSignal:
        return ExternalWorkflowSignal(
            name=self.name,
            correlation_key=self.correlation_key,
            payload=self.payload,
        )


class SignalWorkflowResult(ApplicationModel):
    signal: WorkflowSignalRecord
    run: WorkflowRunRecord


__all__ = [
    "GetWorkflowRunParams",
    "GetWorkflowRunResult",
    "GetWorkflowsParams",
    "GetWorkflowsResult",
    "ListWorkflowRunsParams",
    "ListWorkflowRunsResult",
    "SetWorkflowsParams",
    "SetWorkflowsResult",
    "SignalWorkflowParams",
    "SignalWorkflowResult",
    "StartWorkflowParams",
    "StartWorkflowResult",
]
