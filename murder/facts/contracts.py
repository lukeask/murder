"""Typed contracts for immutable retained facts and projection inputs.

Only completed outcomes intended for audit, projections, subscriptions, or
independent observers belong here.  Workflow signals, terminal data, session
commands, queries, routing decisions, and policy decisions have their own
owners and intentionally have no conversion into these contracts.

``RetainedFactDraft.kind`` is derived from the typed payload discriminator.
Feature-private or not-yet-registered fact types use ``PrivateFactPayload``
rather than a free-form kind paired with an unchecked dict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union, get_args
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    JsonValue,
    Tag,
    TypeAdapter,
    computed_field,
    model_validator,
)

from murder.contracts.common import Correlation, Principal


class FactContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AggregateRef(FactContract):
    kind: str = Field(min_length=1, max_length=100)
    id: UUID
    revision: int | None = Field(default=None, ge=0)


class FactActor(FactContract):
    """Attributed actor on a retained fact (evidence, not an auth subject).

    Prefer shared ``Principal`` kinds when writing new facts via
    :func:`fact_actor`. The kind field remains an open string so historical
    audit rows (and non-principal attributions such as activity workers) stay
    loadable.
    """

    kind: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=500)


# Shared correlation identity; alias retained for fact call sites.
FactCorrelation = Correlation


def fact_actor(principal: Principal) -> FactActor:
    """Project a shared ``Principal`` into retained-fact actor evidence."""

    return FactActor(kind=str(principal.kind), id=principal.id)


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


ActivityStateFactType = Literal[
    "activity.created",
    "activity.routed",
    "activity.admitted",
    "activity.claimed",
    "activity.claim_renewed",
    "activity.started",
    "activity.claim_reaped",
]


class ActivityStateChangedPayload(FactContract):
    type: ActivityStateFactType
    activity_id: UUID
    workflow_id: UUID
    status: str = Field(min_length=1)
    revision: int = Field(ge=0)
    attempt: int = Field(ge=0)
    claim_fence: int = Field(ge=0)


ActivityOutcomeFactType = Literal[
    "activity.succeeded",
    "activity.failed",
    "activity.cancelled",
]


class ActivityOutcomeRecordedPayload(FactContract):
    type: ActivityOutcomeFactType
    workflow_id: UUID
    result_id: UUID
    attempt: int = Field(ge=0)
    outcome: dict[str, JsonValue]


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


class FactPayloadRegistry:
    """Closed registry of public retained-fact payload models.

    Feature-private or not-yet-promoted kinds use ``PrivateFactPayload`` until
    they are registered here and added to the ``FactPayload`` union. Callers
    must not pair a free-form envelope kind with an unchecked dict.
    """

    def __init__(self) -> None:
        self._models: dict[str, type[FactContract]] = {}

    def register(self, model: type[FactContract]) -> type[FactContract]:
        type_field = model.model_fields.get("type")
        if type_field is None:
            raise ValueError(f"{model.__name__} must declare a Literal type discriminator")
        literals = get_args(type_field.annotation)
        if not literals:
            raise ValueError(f"{model.__name__}.type must be a Literal[...]")
        for literal in literals:
            if not isinstance(literal, str):
                raise ValueError(f"{model.__name__}.type literals must be strings")
            existing = self._models.get(literal)
            if existing is not None and existing is not model:
                raise ValueError(
                    f"fact type {literal!r} is already registered on {existing.__name__}"
                )
            self._models[literal] = model
        return model

    def register_many(self, *models: type[FactContract]) -> None:
        for model in models:
            self.register(model)

    @property
    def public_types(self) -> frozenset[str]:
        return frozenset(self._models)

    def is_public(self, kind: str) -> bool:
        return kind in self._models

    def model_for(self, kind: str) -> type[FactContract] | None:
        return self._models.get(kind)


FACT_PAYLOAD_REGISTRY = FactPayloadRegistry()


class PrivateFactPayload(FactContract):
    """Explicit escape hatch for feature-private retained facts.

    The public envelope kind is ``kind``; ``data`` is the stored payload body.
    Prefer promoting frequent private kinds into ``FACT_PAYLOAD_REGISTRY`` and
    the closed ``FactPayload`` union.
    """

    type: Literal["fact.private"] = "fact.private"
    kind: str = Field(min_length=1, max_length=200)
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_registered_public_kinds(self) -> PrivateFactPayload:
        if FACT_PAYLOAD_REGISTRY.is_public(self.kind):
            raise ValueError(
                f"private fact kind {self.kind!r} is registered as a public "
                "FactPayload; construct the typed public payload instead"
            )
        return self


_PUBLIC_FACT_PAYLOADS = (
    WorkflowStartedPayload,
    WorkflowTransitionAppliedPayload,
    WorkflowStateMigratedPayload,
    WorkflowOutcomeRecordedPayload,
    ActivityStateChangedPayload,
    ActivityOutcomeRecordedPayload,
    WriterLeaseAcquiredPayload,
    WriterLeaseRenewedPayload,
    WriterLeaseReleasedPayload,
    WriterLeaseRevokedPayload,
    WriterLeaseTakeoverPayload,
    SessionLifecyclePayload,
)
FACT_PAYLOAD_REGISTRY.register_many(*_PUBLIC_FACT_PAYLOADS)


def _fact_payload_discriminator(value: Any) -> str:
    type_name = value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    if isinstance(type_name, str) and FACT_PAYLOAD_REGISTRY.is_public(type_name):
        return type_name
    return "fact.private"


FactPayload = Annotated[
    Union[
        Annotated[WorkflowStartedPayload, Tag("workflow.started")],
        Annotated[WorkflowTransitionAppliedPayload, Tag("workflow.transition.applied")],
        Annotated[WorkflowStateMigratedPayload, Tag("workflow.state.migrated")],
        Annotated[WorkflowOutcomeRecordedPayload, Tag("workflow.outcome.recorded")],
        Annotated[ActivityStateChangedPayload, Tag("activity.created")],
        Annotated[ActivityStateChangedPayload, Tag("activity.routed")],
        Annotated[ActivityStateChangedPayload, Tag("activity.admitted")],
        Annotated[ActivityStateChangedPayload, Tag("activity.claimed")],
        Annotated[ActivityStateChangedPayload, Tag("activity.claim_renewed")],
        Annotated[ActivityStateChangedPayload, Tag("activity.started")],
        Annotated[ActivityStateChangedPayload, Tag("activity.claim_reaped")],
        Annotated[ActivityOutcomeRecordedPayload, Tag("activity.succeeded")],
        Annotated[ActivityOutcomeRecordedPayload, Tag("activity.failed")],
        Annotated[ActivityOutcomeRecordedPayload, Tag("activity.cancelled")],
        Annotated[WriterLeaseAcquiredPayload, Tag("session.writer.acquired")],
        Annotated[WriterLeaseRenewedPayload, Tag("session.writer.renewed")],
        Annotated[WriterLeaseReleasedPayload, Tag("session.writer.released")],
        Annotated[WriterLeaseRevokedPayload, Tag("session.writer.revoked")],
        Annotated[WriterLeaseTakeoverPayload, Tag("session.writer.takeover")],
        Annotated[SessionLifecyclePayload, Tag("session.ready")],
        Annotated[SessionLifecyclePayload, Tag("session.stopped")],
        Annotated[SessionLifecyclePayload, Tag("session.failed")],
        Annotated[SessionLifecyclePayload, Tag("session.lost")],
        Annotated[PrivateFactPayload, Tag("fact.private")],
    ],
    Discriminator(_fact_payload_discriminator),
]
FACT_PAYLOAD_ADAPTER: TypeAdapter[FactPayload] = TypeAdapter(FactPayload)


def fact_kind(payload: FactPayload) -> str:
    """Public envelope kind derived from a typed retained-fact payload."""

    if isinstance(payload, PrivateFactPayload):
        return payload.kind
    return payload.type


def fact_payload_storage(payload: FactPayload) -> dict[str, JsonValue]:
    """JSON body stored in ``retained_facts.payload_json``.

    Public payloads keep their discriminator in the body. Private payloads store
    only ``data``; the envelope kind column carries ``PrivateFactPayload.kind``.
    """

    if isinstance(payload, PrivateFactPayload):
        return dict(payload.data)
    dumped = payload.model_dump(mode="json")
    return dumped  # type: ignore[return-value]


def fact_payload_from_storage(kind: str, raw: dict[str, JsonValue]) -> FactPayload:
    """Reconstruct a typed payload from the kind column and stored JSON body."""

    if raw.get("type") == "fact.private":
        return PrivateFactPayload.model_validate(raw)
    if FACT_PAYLOAD_REGISTRY.is_public(kind):
        body = raw if "type" in raw else {**raw, "type": kind}
        return FACT_PAYLOAD_ADAPTER.validate_python(body)
    return PrivateFactPayload(kind=kind, data=raw)


class RetainedFactDraft(FactContract):
    fact_id: UUID = Field(default_factory=uuid4)
    schema_version: int = Field(default=1, ge=1)
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    aggregate: AggregateRef | None = None
    actor: FactActor
    correlation: FactCorrelation
    payload: FactPayload

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> str:
        return fact_kind(self.payload)


class FactEnvelope(FactContract):
    """Stored retained-fact record; payload body matches ``fact_payload_storage``."""

    sequence: int = Field(ge=1)
    fact_id: UUID
    kind: str = Field(min_length=1, max_length=200)
    schema_version: int = Field(default=1, ge=1)
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    aggregate: AggregateRef | None = None
    actor: FactActor
    correlation: FactCorrelation
    payload: dict[str, JsonValue]


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
    "ActivityOutcomeFactType",
    "ActivityOutcomeRecordedPayload",
    "ActivityStateChangedPayload",
    "ActivityStateFactType",
    "FACT_PAYLOAD_ADAPTER",
    "FACT_PAYLOAD_REGISTRY",
    "FactActor",
    "FactContract",
    "FactCorrelation",
    "FactEnvelope",
    "FactPayload",
    "FactPayloadRegistry",
    "PrivateFactPayload",
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
    "fact_actor",
    "fact_kind",
    "fact_payload_from_storage",
    "fact_payload_storage",
]
