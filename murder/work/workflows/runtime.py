"""Typed contracts for the persisted workflow state-machine runtime.

Workflow code receives a finite, already-persisted decision input and returns an
immutable transition plan.  This module deliberately contains no persistence,
clock, network, subprocess, or filesystem access: a workflow decision is a pure
function and recovery reloads current state instead of replaying Python code.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Generic, Literal, Protocol, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from murder.contracts.common import (
    Correlation,
    Principal,
    PrincipalKind,  # noqa: F401 — re-exported for workflow call sites
    StrEnum,
)
from murder.permissions.contracts import GrantScope, PermissionPrincipal

# Compatibility spelling for workflow call sites.
PrincipalRef = Principal


class WorkflowContract(BaseModel):
    """Immutable boundary and persistence contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class WorkflowStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class VersionedState(WorkflowContract):
    schema_name: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    value: dict[str, JsonValue]


class WorkflowRunRecord(WorkflowContract):
    """Authoritative current record for one workflow execution."""

    workflow_id: UUID
    definition_name: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    status: WorkflowStatus
    revision: int = Field(ge=0)
    state: VersionedState
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_by: PrincipalRef
    correlation: Correlation
    terminal_reason: str | None = None

    # Static ticket-DAG compatibility metadata.  These fields locate the
    # materialized view; they are never consulted to derive run truth.
    parent_ticket_id: str | None = None
    definition_snapshot: dict[str, JsonValue] | None = None
    stage_map: dict[str, str] = Field(default_factory=dict)

    @property
    def name(self) -> str:
        """Legacy spelling retained for callers during the migration."""

        return self.definition_name

    @property
    def definition_json(self) -> str:
        """Legacy serialized snapshot view retained for existing callers."""

        return json.dumps(self.definition_snapshot or {}, separators=(",", ":"), sort_keys=True)


class WorkflowStateMigrationRecord(WorkflowContract):
    migration_id: UUID
    workflow_id: UUID
    migration_name: str = Field(min_length=1)
    from_schema_name: str
    from_schema_version: int = Field(ge=1)
    to_schema_name: str
    to_schema_version: int = Field(ge=1)
    from_revision: int = Field(ge=0)
    to_revision: int = Field(ge=1)
    migrated_at: AwareDatetime


class StageStatus(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    REQUESTED = "requested"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageRunState(WorkflowContract):
    stage_id: str = Field(min_length=1)
    status: StageStatus
    activity_id: UUID | None = None
    session_id: UUID | None = None
    attempts: int = Field(default=0, ge=0)
    result_ref: UUID | None = None
    error: str | None = None


class StaticDagWorkflowStateV1(WorkflowContract):
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    stages: tuple[StageRunState, ...]
    output: dict[str, JsonValue] | None = None


# Exactly the six durable wait variants from the target architecture.
class ActivityWait(WorkflowContract):
    type: Literal["activity"] = "activity"
    activity_id: UUID


class ApprovalWait(WorkflowContract):
    type: Literal["approval"] = "approval"
    approval_id: UUID


class TimerWait(WorkflowContract):
    type: Literal["timer"] = "timer"
    timer_id: UUID
    due_at: AwareDatetime


class ExternalSignalWait(WorkflowContract):
    type: Literal["external_signal"] = "external_signal"
    signal_name: str = Field(min_length=1)
    correlation_key: str | None = None


class ResourceWait(WorkflowContract):
    type: Literal["resource"] = "resource"
    resource_kind: str = Field(min_length=1)
    selector: dict[str, JsonValue]


class JoinWait(WorkflowContract):
    type: Literal["join"] = "join"
    activity_ids: tuple[UUID, ...]
    mode: Literal["all", "any", "threshold"] = "all"
    threshold: int | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.activity_ids:
            raise ValueError("join wait requires at least one activity")
        if len(set(self.activity_ids)) != len(self.activity_ids):
            raise ValueError("join wait activity_ids must be unique")
        if self.mode == "threshold":
            if self.threshold is None or not 1 <= self.threshold <= len(self.activity_ids):
                raise ValueError("threshold must be between 1 and the activity count")
        elif self.threshold is not None:
            raise ValueError("threshold is only valid for threshold joins")


WaitSpec = Annotated[
    ActivityWait | ApprovalWait | TimerWait | ExternalSignalWait | ResourceWait | JoinWait,
    Field(discriminator="type"),
]


class WorkflowWaitRecord(WorkflowContract):
    wait_id: UUID
    workflow_id: UUID
    created_at: AwareDatetime
    spec: WaitSpec
    satisfied_at: AwareDatetime | None = None
    satisfied_by_signal_id: UUID | None = None


# Exactly the four addressed durable signal variants from the target.
class ActivityFinishedSignal(WorkflowContract):
    type: Literal["activity.finished"] = "activity.finished"
    activity_id: UUID
    result_id: UUID


class ApprovalResolvedSignal(WorkflowContract):
    type: Literal["approval.resolved"] = "approval.resolved"
    approval_id: UUID
    decision_id: UUID


class TimerFiredSignal(WorkflowContract):
    type: Literal["timer.fired"] = "timer.fired"
    timer_id: UUID


class ExternalWorkflowSignal(WorkflowContract):
    type: Literal["external"] = "external"
    name: str = Field(min_length=1)
    correlation_key: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)


WorkflowSignalPayload = Annotated[
    ActivityFinishedSignal | ApprovalResolvedSignal | TimerFiredSignal | ExternalWorkflowSignal,
    Field(discriminator="type"),
]


class WorkflowSignalRecord(WorkflowContract):
    signal_id: UUID
    workflow_id: UUID
    deduplication_key: str = Field(min_length=1)
    created_at: AwareDatetime
    payload: WorkflowSignalPayload
    consumed_at: AwareDatetime | None = None
    consumed_at_revision: int | None = Field(default=None, ge=1)


class WorkflowDecisionInput(WorkflowContract):
    run: WorkflowRunRecord
    waits: tuple[WorkflowWaitRecord, ...]
    signals: tuple[WorkflowSignalRecord, ...]
    now: AwareDatetime


class StateReplacement(WorkflowContract):
    expected_revision: int = Field(ge=0)
    status: WorkflowStatus
    state: VersionedState
    terminal_reason: str | None = None


class AggregateRef(WorkflowContract):
    kind: str = Field(min_length=1)
    id: UUID
    revision: int | None = Field(default=None, ge=0)


class FactDraft(WorkflowContract):
    kind: str = Field(min_length=1)
    aggregate: AggregateRef | None = None
    payload: dict[str, JsonValue]


class ExecutionRequirements(WorkflowContract):
    capability_tags: frozenset[str] = frozenset()
    preferred_harnesses: tuple[str, ...] = ()
    excluded_harnesses: frozenset[str] = frozenset()
    preferred_models: tuple[str, ...] = ()
    require_structured_protocol: bool = False
    require_terminal: bool = False
    reusable_session: bool = True
    session_strategy: Literal[
        "new", "reuse_if_compatible", "require_existing"
    ] | None = None
    worktree: str | None = None
    max_parallelism_group: str | None = None
    policy_profile: str | None = None


class RunAgentTurnActivity(WorkflowContract):
    type: Literal["agent.run_turn"] = "agent.run_turn"
    instructions: str
    context_refs: tuple[str, ...] = ()
    requirements: ExecutionRequirements


class RunReviewActivity(WorkflowContract):
    type: Literal["agent.review"] = "agent.review"
    subject_ref: str
    instructions: str
    requirements: ExecutionRequirements


ActivityPayload = Annotated[
    RunAgentTurnActivity | RunReviewActivity,
    Field(discriminator="type"),
]


class ActivityRequestDraft(WorkflowContract):
    activity_id: UUID
    payload: ActivityPayload
    idempotency_key: str = Field(min_length=1)
    priority: int = 0
    retry_policy: str = "default"
    max_attempts: int = Field(default=3, ge=1)


class ApprovalRequestDraft(WorkflowContract):
    approval_id: UUID
    operation_digest: str = Field(min_length=1)
    summary: str
    details: dict[str, JsonValue] = Field(default_factory=dict)
    required_reviewers: tuple[Literal["human", "llm"], ...]
    policy: Literal["any", "all", "human_required"]
    requested_by: PermissionPrincipal
    grant_scope: GrantScope


class WorkflowTransitionPlan(WorkflowContract):
    state: StateReplacement
    consume_signal_ids: tuple[UUID, ...] = ()
    replace_waits: tuple[WaitSpec, ...] = ()
    activities: tuple[ActivityRequestDraft, ...] = ()
    approvals: tuple[ApprovalRequestDraft, ...] = ()
    facts: tuple[FactDraft, ...] = ()


StateT = TypeVar("StateT", bound=WorkflowContract)


class WorkflowMachine(Protocol, Generic[StateT]):
    definition_name: str
    definition_version: int
    state_model: type[StateT]

    def initialize(
        self,
        *,
        inputs: dict[str, JsonValue],
        now: datetime,
    ) -> StateT: ...

    def decide(
        self,
        *,
        state: StateT,
        waits: tuple[WorkflowWaitRecord, ...],
        signals: tuple[WorkflowSignalRecord, ...],
        now: datetime,
        current_revision: int,
    ) -> WorkflowTransitionPlan: ...


def versioned_state(
    value: WorkflowContract,
    *,
    schema_name: str,
    schema_version: int,
) -> VersionedState:
    """Wrap a concrete typed state in its persistence envelope."""

    return VersionedState(
        schema_name=schema_name,
        schema_version=schema_version,
        value=value.model_dump(mode="json"),
    )
