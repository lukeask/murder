"""Typed trigger specifications; observers produce occurrences, never work."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from murder.work.workflows.runtime import WorkflowContract


class ManualTrigger(WorkflowContract):
    type: Literal["manual"] = "manual"
    command: str = Field(min_length=1)


class RepositoryTrigger(WorkflowContract):
    type: Literal["repository"] = "repository"
    repository_id: UUID
    paths: tuple[str, ...] = ()
    change_kinds: frozenset[str] = frozenset({"modified"})
    debounce_seconds: int = Field(default=0, ge=0)


class CronTrigger(WorkflowContract):
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=1)
    timezone: str = Field(default="UTC", min_length=1)


class FactTrigger(WorkflowContract):
    type: Literal["fact"] = "fact"
    fact_kind: str = Field(min_length=1)
    predicate: dict[str, JsonValue] = Field(default_factory=dict)


TriggerSpec = Annotated[
    CronTrigger | FactTrigger | RepositoryTrigger | ManualTrigger,
    Field(discriminator="type"),
]


class StartWorkflowTarget(WorkflowContract):
    type: Literal["start_workflow"] = "start_workflow"
    definition_name: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)


class SignalWorkflowTarget(WorkflowContract):
    type: Literal["signal_workflow"] = "signal_workflow"
    workflow_id: UUID
    signal_name: str = Field(min_length=1)
    correlation_key: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)


TriggerTarget = Annotated[
    StartWorkflowTarget | SignalWorkflowTarget,
    Field(discriminator="type"),
]


class TriggerDefinition(WorkflowContract):
    trigger_id: UUID
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    spec: TriggerSpec
    target: TriggerTarget
    dedup_window_seconds: int = Field(default=0, ge=0)
    enabled: bool = True
    created_at: AwareDatetime
    last_fired_at: AwareDatetime | None = None


TriggerRecord = TriggerDefinition


class TriggerFiringRecord(WorkflowContract):
    firing_id: UUID
    trigger_id: UUID
    occurrence_key: str = Field(min_length=1)
    fired_at: AwareDatetime
    workflow_id: UUID
