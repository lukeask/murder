"""Typed contracts for durable, claimed external work."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from murder.work.workflows.runtime import (
    ActivityPayload,
    ExecutionRequirements,
    WorkflowContract,
)


class ActivityStatus(str, Enum):
    PENDING = "pending"
    ROUTING = "routing"
    WAITING_ADMISSION = "waiting_admission"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SessionStrategy(str, Enum):
    NEW = "new"
    REUSE_IF_COMPATIBLE = "reuse_if_compatible"
    REQUIRE_EXISTING = "require_existing"


class ModelAssignment(WorkflowContract):
    role: Literal[
        "primary",
        "planner",
        "reviewer",
        "critic",
        "summarizer",
        "specialist",
    ]
    harness: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str | None = None


class ExecutionRoute(WorkflowContract):
    route_id: UUID
    assignments: tuple[ModelAssignment, ...]
    session_strategy: SessionStrategy
    selected_session_id: UUID | None = None
    structured_protocol: bool
    terminal_fallback: bool
    capability_revision: int = Field(ge=0)
    usage_revision: int = Field(ge=0)
    rationale: str = Field(min_length=1)


class ActivityRecord(WorkflowContract):
    activity_id: UUID
    workflow_id: UUID
    workflow_revision: int = Field(ge=1)
    ordinal: int = Field(ge=0)
    revision: int = Field(ge=0)
    status: ActivityStatus
    payload: ActivityPayload
    requirements: ExecutionRequirements
    idempotency_key: str = Field(min_length=1)
    priority: int = 0
    retry_policy: str = Field(min_length=1)
    max_attempts: int = Field(ge=1)
    route_id: UUID | None = None
    route: ExecutionRoute | None = None
    session_id: UUID | None = None
    attempts: int = Field(default=0, ge=0)
    claimed_by: str | None = None
    claim_fence: int = Field(default=0, ge=0)
    lease_expires_at: AwareDatetime | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ActivityClaim(WorkflowContract):
    activity_id: UUID
    owner: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    fence: int = Field(ge=1)
    claimed_at: AwareDatetime
    expires_at: AwareDatetime


class ActivitySuccess(WorkflowContract):
    type: Literal["success"] = "success"
    output: dict[str, JsonValue] = Field(default_factory=dict)


class ActivityFailure(WorkflowContract):
    type: Literal["failure"] = "failure"
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ActivityCancelled(WorkflowContract):
    type: Literal["cancelled"] = "cancelled"
    reason: str | None = None


ActivityOutcome = Annotated[
    ActivitySuccess | ActivityFailure | ActivityCancelled,
    Field(discriminator="type"),
]


class ActivityResultRecord(WorkflowContract):
    result_id: UUID
    activity_id: UUID
    attempt: int = Field(ge=0)
    outcome: ActivityOutcome
    completed_at: AwareDatetime
