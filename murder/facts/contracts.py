"""Typed contracts for immutable retained facts and projection inputs.

Only completed outcomes intended for audit, projections, subscriptions, or
independent observers belong here.  Workflow signals, terminal data, session
commands, queries, routing decisions, and policy decisions have their own
owners and intentionally have no conversion into these contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class FactContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AggregateRef(FactContract):
    kind: str = Field(min_length=1, max_length=100)
    id: UUID
    revision: int | None = Field(default=None, ge=0)


class FactActor(FactContract):
    kind: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=500)


class FactCorrelation(FactContract):
    correlation_id: UUID
    causation_id: UUID | None = None
    trace_id: UUID | None = None


class WorkflowTransitionAppliedPayload(FactContract):
    """Representative closed fact payload owned by the workflow feature."""

    type: Literal["workflow.transition.applied"] = "workflow.transition.applied"
    workflow_id: UUID
    from_status: str = Field(min_length=1)
    to_status: str = Field(min_length=1)
    revision: int = Field(ge=1)


class WorkflowStartedPayload(FactContract):
    type: Literal["workflow.started"] = "workflow.started"
    workflow_id: UUID
    definition_name: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    status: str = Field(min_length=1)


class WorkflowStateMigratedPayload(FactContract):
    type: Literal["workflow.state.migrated"] = "workflow.state.migrated"
    workflow_id: UUID
    migration_name: str = Field(min_length=1)
    from_schema_name: str = Field(min_length=1)
    from_schema_version: int = Field(ge=1)
    to_schema_name: str = Field(min_length=1)
    to_schema_version: int = Field(ge=1)
    revision: int = Field(ge=1)


class WorkflowOutcomeRecordedPayload(FactContract):
    """Representative terminal workflow outcome payload."""

    type: Literal["workflow.outcome.recorded"] = "workflow.outcome.recorded"
    workflow_id: UUID
    definition_name: str = Field(min_length=1)
    outcome: Literal["completed", "failed", "cancelled"]
    reason: str | None = None


class ActivityStateChangedPayload(FactContract):
    type: Literal["activity.state.changed"] = "activity.state.changed"
    activity_id: UUID
    workflow_id: UUID
    operation: Literal[
        "created",
        "routed",
        "admitted",
        "claimed",
        "claim_renewed",
        "started",
        "claim_reaped",
    ]
    status: str = Field(min_length=1)
    revision: int = Field(ge=0)
    attempt: int = Field(ge=0)
    claim_fence: int = Field(ge=0)


class WriterLeaseAcquiredPayload(FactContract):
    type: Literal["session.writer.acquired"] = "session.writer.acquired"
    session_id: UUID
    lease_id: UUID
    mode: Literal["structured", "raw_terminal"]
    fence: int = Field(ge=1)
    expires_at: AwareDatetime


class WriterLeaseRenewedPayload(FactContract):
    type: Literal["session.writer.renewed"] = "session.writer.renewed"
    session_id: UUID
    lease_id: UUID
    mode: Literal["structured", "raw_terminal"]
    fence: int = Field(ge=1)
    expires_at: AwareDatetime


class WriterLeaseReleasedPayload(FactContract):
    type: Literal["session.writer.released"] = "session.writer.released"
    session_id: UUID
    lease_id: UUID
    mode: Literal["structured", "raw_terminal"]
    fence: int = Field(ge=1)
    reason: str = Field(min_length=1)


class WriterLeaseRevokedPayload(FactContract):
    type: Literal["session.writer.revoked"] = "session.writer.revoked"
    session_id: UUID
    lease_id: UUID
    mode: Literal["structured", "raw_terminal"]
    fence: int = Field(ge=1)
    reason: str = Field(min_length=1)


class WriterLeaseTakeoverPayload(FactContract):
    type: Literal["session.writer.takeover"] = "session.writer.takeover"
    session_id: UUID
    previous_lease_id: UUID
    previous_fence: int = Field(ge=1)
    lease_id: UUID
    mode: Literal["structured", "raw_terminal"]
    fence: int = Field(ge=1)
    reason: str = Field(min_length=1)
    expires_at: AwareDatetime


class SessionLifecyclePayload(FactContract):
    type: Literal[
        "session.ready",
        "session.stopped",
        "session.failed",
        "session.lost",
    ]
    session_id: UUID
    from_status: str | None = None
    to_status: Literal["ready", "stopped", "failed", "lost"]
    revision: int = Field(ge=0)
    harness: str = Field(min_length=1)
    transport: Literal["tmux", "app_server", "subprocess"]


FactPayload = Annotated[
    WorkflowStartedPayload
    | WorkflowTransitionAppliedPayload
    | WorkflowStateMigratedPayload
    | WorkflowOutcomeRecordedPayload
    | ActivityStateChangedPayload
    | WriterLeaseAcquiredPayload
    | WriterLeaseRenewedPayload
    | WriterLeaseReleasedPayload
    | WriterLeaseRevokedPayload
    | WriterLeaseTakeoverPayload
    | SessionLifecyclePayload,
    Field(discriminator="type"),
]
FACT_PAYLOAD_ADAPTER: TypeAdapter[FactPayload] = TypeAdapter(FactPayload)


class RetainedFactDraft(FactContract):
    fact_id: UUID = Field(default_factory=uuid4)
    kind: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(default=1, ge=1)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    aggregate: AggregateRef | None = None
    actor: FactActor
    correlation: FactCorrelation
    payload: dict[str, JsonValue]


class FactEnvelope(RetainedFactDraft):
    sequence: int = Field(ge=1)
    recorded_at: AwareDatetime


# Compatibility spelling for early Phase-6 callers. New code should use the
# target-architecture name ``FactEnvelope``.
RetainedFactRecord = FactEnvelope


class ProjectionInputDraft(FactContract):
    """A key-only projection invalidation appended with its source fact."""

    input_id: UUID = Field(default_factory=uuid4)
    projection: str = Field(min_length=1, max_length=200)
    subject_key: str = Field(min_length=1, max_length=500)
    generation: int = Field(ge=0)


class ProjectionInputRecord(ProjectionInputDraft):
    sequence: int = Field(ge=1)
    source_fact_id: UUID | None = None
    created_at: AwareDatetime


__all__ = [
    "AggregateRef",
    "ActivityStateChangedPayload",
    "FACT_PAYLOAD_ADAPTER",
    "FactActor",
    "FactContract",
    "FactCorrelation",
    "FactEnvelope",
    "FactPayload",
    "ProjectionInputDraft",
    "ProjectionInputRecord",
    "RetainedFactDraft",
    "RetainedFactRecord",
    "WorkflowOutcomeRecordedPayload",
    "WorkflowStartedPayload",
    "WorkflowStateMigratedPayload",
    "WorkflowTransitionAppliedPayload",
    "SessionLifecyclePayload",
    "WriterLeaseAcquiredPayload",
    "WriterLeaseReleasedPayload",
    "WriterLeaseRenewedPayload",
    "WriterLeaseRevokedPayload",
    "WriterLeaseTakeoverPayload",
]
