"""Verified exhaustive discovery of an interactive harness model picker."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import (
    DismissOverlay,
    DuplicatePolicy,
    NavigateModelPicker,
    OpenModelPicker,
)
from murder.llm.harness_control.model.observations import (
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


class ModelDiscoveryPhase(Enum):
    CREATED = auto()
    AWAITING_PICKER = auto()
    SCANNING = auto()
    AWAITING_STEP = auto()
    AWAITING_DISMISSAL = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class DiscoverModelsRequest:
    deadline: timedelta


@dataclass(frozen=True, slots=True)
class DiscoverModelsOperation:
    envelope: OperationEnvelope[ModelDiscoveryPhase]
    request: DiscoverModelsRequest
    picker_action_id: str | None = None
    picker_baseline_revision: ObservationRevision | None = None
    navigation_action_ids: tuple[str, ...] = ()
    navigation_baseline_revision: ObservationRevision | None = None
    dismissal_action_id: str | None = None
    dismissal_baseline_revision: ObservationRevision | None = None
    start_model_id: str | None = None
    models: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoverModelsResult:
    operation_id: str
    models: tuple[tuple[str, str], ...]
    succeeded: bool
    warnings: tuple[str, ...] = ()


def _after(snapshot: ObservationSnapshot, baseline: ObservationRevision | None) -> bool:
    return baseline is not None and snapshot.revision > baseline


def _picker_visible(snapshot: ObservationSnapshot) -> bool:
    return bool(
        snapshot.surface.knowledge is Knowledge.PRESENT
        and snapshot.surface.value is not None
        and snapshot.surface.value.primary is SurfaceKind.MODEL_PICKER
        and snapshot.model_configuration.knowledge is Knowledge.PRESENT
        and snapshot.model_configuration.value is not None
    )


def _safe_composer(snapshot: ObservationSnapshot) -> bool:
    return bool(
        snapshot.surface.knowledge is Knowledge.PRESENT
        and snapshot.surface.value is not None
        and snapshot.surface.value.primary in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}
    )


def _model_slash_command_is_pending(snapshot: ObservationSnapshot) -> bool:
    return bool(
        snapshot.composer.knowledge is Knowledge.PRESENT
        and snapshot.composer.value is not None
        and snapshot.composer.value.text is not None
        and snapshot.composer.value.text.strip().startswith("/model")
    )


def _highlighted(snapshot: ObservationSnapshot) -> str | None:
    if not _picker_visible(snapshot):
        return None
    assert snapshot.model_configuration.value is not None
    highlighted = next(
        (
            choice
            for choice in snapshot.model_configuration.value.available
            if choice.highlighted
        ),
        None,
    )
    if highlighted is not None:
        return f"{highlighted.stable_choice_id or ''}\x1f{highlighted.label}"
    highlighted_model_id = snapshot.model_configuration.value.highlighted_model_id
    return highlighted_model_id if isinstance(highlighted_model_id, str) else None


def _action(
    operation: DiscoverModelsOperation, kind: str
) -> OpenModelPicker | NavigateModelPicker | DismissOverlay:
    action_id = str(uuid4())
    operation_id = operation.envelope.operation_id
    duplicate_policy = DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS
    if kind == "open":
        return OpenModelPicker(action_id, operation_id, duplicate_policy)
    if kind == "down":
        return NavigateModelPicker(action_id, operation_id, duplicate_policy, "down")
    if kind == "dismiss":
        return DismissOverlay(action_id, operation_id, duplicate_policy, "model_picker")
    raise ValueError(kind)


def reconcile_model_discovery(  # noqa: PLR0911, PLR0912 -- typed phase machine
    operation: DiscoverModelsOperation,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> ControllerDecision:
    phase = operation.envelope.phase
    if snapshot.health.requires_escalation:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            ModelDiscoveryPhase.ESCALATED,
            None,
            reason="observation health requires escalation",
        )
    if operation.envelope.deadline is not None and now > operation.envelope.deadline:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            ModelDiscoveryPhase.ESCALATED,
            None,
            reason="model discovery deadline exceeded",
        )
    if phase is ModelDiscoveryPhase.CREATED:
        if _picker_visible(snapshot):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                ModelDiscoveryPhase.SCANNING,
                None,
                reason="use the already visible model picker",
            )
        if not _safe_composer(snapshot):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                reason="wait for a safe composer before opening the model picker",
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            ModelDiscoveryPhase.AWAITING_PICKER,
            _action(operation, "open"),
            "open the interactive model picker",
        )
    if phase is ModelDiscoveryPhase.AWAITING_PICKER:
        if operation.picker_action_id is None or operation.picker_baseline_revision is None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ModelDiscoveryPhase.ESCALATED,
                None,
                reason="model discovery lacks picker action persistence",
            )
        if not _after(snapshot, operation.picker_baseline_revision):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE, phase, None, reason="wait for picker evidence"
            )
        if not _picker_visible(snapshot):
            if _safe_composer(snapshot) and _model_slash_command_is_pending(snapshot):
                return ControllerDecision(
                    ControllerDecisionKind.EMIT_ACTION,
                    ModelDiscoveryPhase.AWAITING_PICKER,
                    _action(operation, "open"),
                    reason="confirm the pending /model slash-command selection",
                )
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ModelDiscoveryPhase.ESCALATED,
                None,
                reason="interactive model picker did not become visible",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            ModelDiscoveryPhase.SCANNING,
            None,
            reason="model picker is visible; begin exhaustive traversal",
        )
    if phase is ModelDiscoveryPhase.SCANNING:
        highlighted = _highlighted(snapshot)
        if highlighted is None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ModelDiscoveryPhase.ESCALATED,
                None,
                reason="model picker cursor is not observable",
            )
        if operation.navigation_action_ids and highlighted == operation.start_model_id:
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                ModelDiscoveryPhase.AWAITING_DISMISSAL,
                _action(operation, "dismiss"),
                "picker traversal returned to its starting row",
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            ModelDiscoveryPhase.AWAITING_STEP,
            _action(operation, "down"),
            "advance one picker row and capture the next viewport",
        )
    if phase is ModelDiscoveryPhase.AWAITING_STEP:
        if not operation.navigation_action_ids or operation.navigation_baseline_revision is None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ModelDiscoveryPhase.ESCALATED,
                None,
                reason="model traversal step lacks persisted action state",
            )
        if not _after(snapshot, operation.navigation_baseline_revision):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                reason="wait for a fresh picker viewport",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            ModelDiscoveryPhase.SCANNING,
            None,
            reason="fresh picker viewport observed",
        )
    if phase is ModelDiscoveryPhase.AWAITING_DISMISSAL:
        if operation.dismissal_action_id is None or operation.dismissal_baseline_revision is None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                ModelDiscoveryPhase.ESCALATED,
                None,
                reason="picker dismissal lacks persisted action state",
            )
        if not _after(snapshot, operation.dismissal_baseline_revision):
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                reason="wait for picker dismissal",
            )
        if _safe_composer(snapshot):
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                ModelDiscoveryPhase.SUCCEEDED,
                None,
                reason="exhaustive model traversal completed and picker was dismissed",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            reason="await a safe composer readback",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL,
        ModelDiscoveryPhase.FAILED,
        None,
        reason=f"invalid model discovery phase {phase.name}",
    )


def _visible_models(snapshot: ObservationSnapshot) -> tuple[tuple[str, str], ...]:
    if not _picker_visible(snapshot):
        return ()
    assert snapshot.model_configuration.value is not None
    return tuple(
        (choice.stable_choice_id, choice.label)
        for choice in snapshot.model_configuration.value.available
        if choice.stable_choice_id is not None
    )


def advance_model_discovery(
    operation: DiscoverModelsOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> DiscoverModelsOperation:
    if not isinstance(decision.next_phase, ModelDiscoveryPhase):
        raise TypeError("model-discovery decision has an invalid next phase")
    phase = decision.next_phase
    status = operation.envelope.status
    if decision.kind is ControllerDecisionKind.SUCCEED:
        status = OperationStatus.SUCCEEDED
    elif decision.kind is ControllerDecisionKind.FAIL:
        status = OperationStatus.FAILED
    elif decision.kind is ControllerDecisionKind.ESCALATE:
        status = OperationStatus.ESCALATED
    elif status is OperationStatus.PENDING:
        status = OperationStatus.RUNNING
    history = operation.envelope.action_history
    picker_id, picker_base = operation.picker_action_id, operation.picker_baseline_revision
    nav_ids, nav_base = operation.navigation_action_ids, operation.navigation_baseline_revision
    dismiss_id, dismiss_base = (
        operation.dismissal_action_id,
        operation.dismissal_baseline_revision,
    )
    start = operation.start_model_id
    if decision.action is not None:
        history = (*history, decision.action.action_id)
        if phase is ModelDiscoveryPhase.AWAITING_PICKER:
            picker_id, picker_base = decision.action.action_id, snapshot.revision
        elif phase is ModelDiscoveryPhase.AWAITING_STEP:
            nav_ids, nav_base = (*nav_ids, decision.action.action_id), snapshot.revision
            if start is None:
                start = _highlighted(snapshot)
        elif phase is ModelDiscoveryPhase.AWAITING_DISMISSAL:
            dismiss_id, dismiss_base = decision.action.action_id, snapshot.revision
    models = tuple(dict.fromkeys((*operation.models, *_visible_models(snapshot))))
    return replace(
        operation,
        envelope=replace(
            operation.envelope,
            phase=phase,
            status=status,
            updated_at=now,
            last_observation_revision=snapshot.revision,
            action_history=history,
        ),
        picker_action_id=picker_id,
        picker_baseline_revision=picker_base,
        navigation_action_ids=nav_ids,
        navigation_baseline_revision=nav_base,
        dismissal_action_id=dismiss_id,
        dismissal_baseline_revision=dismiss_base,
        start_model_id=start,
        models=models,
    )


__all__ = [
    "DiscoverModelsOperation",
    "DiscoverModelsRequest",
    "DiscoverModelsResult",
    "ModelDiscoveryPhase",
    "advance_model_discovery",
    "reconcile_model_discovery",
]
