"""Typed operation envelopes, decisions, and durable decision records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Generic, TypeVar

from murder.llm.harness_control.model.actions import (
    ActionId,
    DuplicatePolicy,
    EffectId,
    InputChunk,
    OperationId,
    SemanticAction,
    TerminalEffect,
)
from murder.llm.harness_control.model.observations import ObservationRevision, TurnRef
from murder.llm.harness_control.model.predicates import PredicateResult

PhaseT = TypeVar("PhaseT")


class OperationStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class OperationWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class OperationEnvelope(Generic[PhaseT]):
    operation_id: OperationId
    capability: str
    status: OperationStatus
    phase: PhaseT
    created_at: datetime
    updated_at: datetime
    deadline: datetime | None
    attempt_count: int = 0
    last_observation_revision: ObservationRevision | None = None
    action_history: tuple[ActionId, ...] = ()
    warnings: tuple[OperationWarning, ...] = ()


class ControllerDecisionKind(Enum):
    OBSERVE_MORE = auto()
    EMIT_ACTION = auto()
    SUCCEED = auto()
    FAIL = auto()
    ESCALATE = auto()


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    kind: ControllerDecisionKind
    next_phase: object | None
    action: SemanticAction | None
    reason: str
    predicates: tuple[PredicateResult, ...] = ()
    wake_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.kind is ControllerDecisionKind.EMIT_ACTION and self.action is None:
            raise ValueError("an emit decision requires an action")
        if self.kind is not ControllerDecisionKind.EMIT_ACTION and self.action is not None:
            raise ValueError("only an emit decision may carry an action")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """One durable controller reconciliation step.

    The advanced operation records the selected phase and unsafe action before
    physical terminal emission, making recovery independent of a Python call
    stack that may have disappeared mid-effect.
    """

    operation: object
    decision: ControllerDecision
    emission: object | None = None


@dataclass(frozen=True, slots=True)
class ActionExpectation:
    require_revision_after: ObservationRevision
    acknowledgment_predicates: tuple[str, ...] = ()
    completion_predicates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: ActionId
    operation_id: OperationId
    semantic_action: SemanticAction
    lowered_effects: tuple[TerminalEffect, ...]
    selected_from_revision: ObservationRevision
    requested_at: datetime
    expectation: ActionExpectation
    emitted_at: datetime | None = None
    emission_error: str | None = None

    def __post_init__(self) -> None:
        if self.semantic_action.action_id != self.action_id:
            raise ValueError("action record id must match semantic action")
        if self.semantic_action.operation_id != self.operation_id:
            raise ValueError("action record operation must match semantic action")

    @property
    def duplicate_policy(self) -> DuplicatePolicy:
        return self.semantic_action.duplicate_policy


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    operation_id: OperationId
    observation_revision: ObservationRevision
    phase_before: str
    predicate_results: tuple[PredicateResult, ...]
    selected_decision: ControllerDecisionKind
    selected_action_id: ActionId | None
    reason: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class EffectRecord:
    effect_id: EffectId
    action_id: ActionId
    payload_type: str
    payload: dict[str, object]


class OperationOutcome(Enum):
    SUBMITTED = auto()
    COMPLETED = auto()
    FAILED = auto()
    ESCALATED = auto()
    CANCELLED = auto()


class SubmitPhase(Enum):
    CREATED = auto()
    ESTABLISHING_SAFE_SURFACE = auto()
    CLEARING_COMPOSER = auto()
    INSERTING_PAYLOAD = auto()
    VERIFYING_PAYLOAD = auto()
    READY_TO_COMMIT = auto()
    COMMIT_EMITTED = auto()
    AWAITING_ACKNOWLEDGMENT = auto()
    SUBMISSION_CONFIRMED = auto()
    AWAITING_COMPLETION = auto()
    RECOVERING = auto()
    AMBIGUOUS = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class PromptPayload:
    chunks: tuple[InputChunk, ...]
    normalized_text: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class SubmitPromptRequest:
    payload: PromptPayload
    await_completion: bool
    submission_deadline: timedelta
    completion_deadline: timedelta | None = None


@dataclass(frozen=True, slots=True)
class SubmitPromptOperation:
    envelope: OperationEnvelope[SubmitPhase]
    request: SubmitPromptRequest
    payload_fingerprint: str
    insertion_action_id: ActionId | None = None
    insertion_revision: ObservationRevision | None = None
    clearing_revision: ObservationRevision | None = None
    commit_action_id: ActionId | None = None
    baseline_revision: ObservationRevision | None = None
    baseline_transcript_revision: int | None = None
    acknowledged_turn: TurnRef | None = None
    completion_turn: TurnRef | None = None
    commit_emitted_at: datetime | None = None
    ambiguity_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitPromptResult:
    operation_id: OperationId
    outcome: OperationOutcome
    submitted_turn: TurnRef | None
    completion_turn: TurnRef | None
    warnings: tuple[OperationWarning, ...] = ()
