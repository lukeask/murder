"""Pure safe-surface restoration and verified interrupt reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import DismissOverlay, DuplicatePolicy, SendInterrupt
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    Knowledge,
    ObservationRevision,
    ObservationSnapshot,
    SurfaceKind,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)


class RestorationPhase(Enum):
    CREATED = auto()
    DISMISSING = auto()
    AWAITING_SURFACE = auto()
    SUCCEEDED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class RestoreComposerRequest:
    deadline: timedelta


@dataclass(frozen=True, slots=True)
class RestoreComposerOperation:
    envelope: OperationEnvelope[RestorationPhase]
    request: RestoreComposerRequest
    baseline_revision: ObservationRevision | None = None
    dismissal_action_id: str | None = None


def reconcile_restore_composer(
    op: RestoreComposerOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline and now > op.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            RestorationPhase.ESCALATED,
            None,
            "composer restoration deadline elapsed",
        )
    composer = snapshot.composer
    if (
        composer.knowledge is Knowledge.PRESENT
        and composer.value
        and composer.value.actionability is ComposerActionability.ACTIONABLE
    ):
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            RestorationPhase.SUCCEEDED,
            None,
            "composer actionable from current observation",
        )
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            RestorationPhase.AWAITING_SURFACE,
            None,
            "surface unknown",
        )
    if (
        op.envelope.phase is RestorationPhase.AWAITING_SURFACE
        and op.baseline_revision
        and snapshot.revision <= op.baseline_revision
    ):
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            RestorationPhase.AWAITING_SURFACE,
            None,
            "observation predates overlay dismissal",
        )
    surface = snapshot.surface.value
    if surface.primary in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            RestorationPhase.AWAITING_SURFACE,
            None,
            "composer remains unobservable",
        )
    action = DismissOverlay(
        str(uuid4()),
        op.envelope.operation_id,
        DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
        surface.primary.name,
    )
    return ControllerDecision(
        ControllerDecisionKind.EMIT_ACTION,
        RestorationPhase.AWAITING_SURFACE,
        action,
        "dismiss observed blocking surface",
    )


def advance_restore_composer(
    op: RestoreComposerOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> RestoreComposerOperation:
    status = (
        OperationStatus.SUCCEEDED
        if decision.kind is ControllerDecisionKind.SUCCEED
        else OperationStatus.ESCALATED
        if decision.kind is ControllerDecisionKind.ESCALATE
        else OperationStatus.FAILED
        if decision.kind is ControllerDecisionKind.FAIL
        else OperationStatus.RUNNING
    )
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=decision.next_phase,
            updated_at=now,
            status=status,
            last_observation_revision=snapshot.revision,
            action_history=(*op.envelope.action_history, decision.action.action_id)
            if decision.action is not None
            else op.envelope.action_history,
        ),
        baseline_revision=snapshot.revision
        if isinstance(decision.action, DismissOverlay)
        else op.baseline_revision,
        dismissal_action_id=decision.action.action_id
        if isinstance(decision.action, DismissOverlay)
        else op.dismissal_action_id,
    )


class InterruptPhase(Enum):
    CREATED = auto()
    INTERRUPT_EMITTED = auto()
    AWAITING_ACKNOWLEDGMENT = auto()
    SUCCEEDED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class InterruptRequest:
    deadline: timedelta


@dataclass(frozen=True, slots=True)
class InterruptOperation:
    envelope: OperationEnvelope[InterruptPhase]
    request: InterruptRequest
    baseline_revision: ObservationRevision | None = None
    action_id: str | None = None


def reconcile_interrupt(
    op: InterruptOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline and now > op.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            InterruptPhase.ESCALATED,
            None,
            "interrupt deadline elapsed",
        )
    generation = snapshot.generation
    if generation.knowledge is not Knowledge.PRESENT or generation.value is None:
        phase = (
            InterruptPhase.AWAITING_ACKNOWLEDGMENT
            if op.action_id is not None or op.envelope.phase is not InterruptPhase.CREATED
            else InterruptPhase.CREATED
        )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            f"generation state is {generation.knowledge.name.lower()}",
        )
    if generation.value.active is not True:
        return ControllerDecision(
            ControllerDecisionKind.SUCCEED,
            InterruptPhase.SUCCEEDED,
            None,
            "generation is not active",
        )
    if op.envelope.phase is InterruptPhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            InterruptPhase.INTERRUPT_EMITTED,
            SendInterrupt(
                str(uuid4()),
                op.envelope.operation_id,
                DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
            ),
            "interrupt observed active generation",
        )
    if op.baseline_revision is None or snapshot.revision <= op.baseline_revision:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            InterruptPhase.AWAITING_ACKNOWLEDGMENT,
            None,
            "await fresh interruption evidence",
        )
    return ControllerDecision(
        ControllerDecisionKind.OBSERVE_MORE,
        InterruptPhase.AWAITING_ACKNOWLEDGMENT,
        None,
        "generation remains active after interrupt; do not blindly emit again",
    )


def advance_interrupt(
    op: InterruptOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> InterruptOperation:
    """Record an interrupt action and its observation boundary before emission."""

    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, InterruptPhase)
        else op.envelope.phase
    )
    status = (
        OperationStatus.SUCCEEDED
        if decision.kind is ControllerDecisionKind.SUCCEED
        else OperationStatus.ESCALATED
        if decision.kind is ControllerDecisionKind.ESCALATE
        else OperationStatus.RUNNING
    )
    action_id = op.action_id
    action_history = op.envelope.action_history
    baseline = op.baseline_revision
    if decision.action is not None:
        action_id = decision.action.action_id
        action_history = (*action_history, action_id)
        baseline = snapshot.revision
    return replace(
        op,
        envelope=replace(
            op.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=action_history,
        ),
        baseline_revision=baseline,
        action_id=action_id,
    )
