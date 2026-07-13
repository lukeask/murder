"""Verified, multi-stage model configuration and activation.

Selecting a row is not success.  Some harnesses save model parameters first,
then require reopening ``/model`` before the configured model can be activated.
This pure reconciler owns that semantic state machine; action adapters lower
``SelectModel`` from the current observed surface into the harness's actual
picker, parameter, and confirmation key sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum, auto
from uuid import uuid4

from murder.llm.harness_control.model.actions import (
    DuplicatePolicy,
    OpenModelPicker,
    SelectModel,
)
from murder.llm.harness_control.model.observations import (
    Knowledge,
    ModelConfigurationState,
    ModelState,
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
from murder.llm.harness_control.model.predicates import PredicateResult, TruthValue


@dataclass(frozen=True, slots=True)
class ModelTarget:
    """Requested configuration, distinct from a picker row or active readback."""

    model_id: str
    provider: str | None = None
    effort: str | None = None
    context_mode: str | None = None
    fast_enabled: bool | None = None
    max_mode_enabled: bool | None = None
    thinking_enabled: bool | None = None
    run_mode: str | None = None

    def parameters(self) -> tuple[tuple[str, str | bool], ...]:
        values = (
            ("effort", self.effort),
            ("context_mode", self.context_mode),
            ("fast_enabled", self.fast_enabled),
            ("max_mode_enabled", self.max_mode_enabled),
            ("thinking_enabled", self.thinking_enabled),
            ("run_mode", self.run_mode),
        )
        return tuple((name, value) for name, value in values if value is not None)


@dataclass(frozen=True, slots=True)
class SelectModelRequest:
    target: ModelTarget
    deadline: timedelta


class ModelSelectionPhase(Enum):
    CREATED = auto()
    ENSURING_CONFIGURATION = auto()
    AWAITING_CONFIGURATION_PICKER = auto()
    AWAITING_CONFIGURATION = auto()
    AWAITING_PARAMETER_SELECTION = auto()
    AWAITING_CONFIGURATION_CONFIRMATION = auto()
    REOPENING_PICKER = auto()
    AWAITING_ACTIVATION_PICKER = auto()
    AWAITING_ACTIVE_READBACK = auto()
    AMBIGUOUS = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class SelectModelOperation:
    envelope: OperationEnvelope[ModelSelectionPhase]
    request: SelectModelRequest
    configuration_action_id: str | None = None
    activation_action_id: str | None = None
    configuration_picker_action_id: str | None = None
    parameter_action_id: str | None = None
    confirmation_action_id: str | None = None
    activation_picker_action_id: str | None = None
    configuration_baseline_revision: ObservationRevision | None = None
    activation_baseline_revision: ObservationRevision | None = None
    configuration_picker_baseline_revision: ObservationRevision | None = None
    parameter_baseline_revision: ObservationRevision | None = None
    confirmation_baseline_revision: ObservationRevision | None = None
    configuration_acknowledged: bool = False
    activation_picker_baseline_revision: ObservationRevision | None = None
    ambiguity_reason: str | None = None


class ModelSelectionOutcome(Enum):
    ACTIVATED = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class SelectModelResult:
    operation_id: str
    outcome: ModelSelectionOutcome
    active_model: ModelState | None
    warnings: tuple[str, ...] = ()


def _predicate(
    snapshot: ObservationSnapshot,
    predicate_id: str,
    value: TruthValue,
    explanation: str,
) -> PredicateResult:
    refs = ()
    if predicate_id.startswith("active_"):
        refs = snapshot.active_model.evidence
    elif predicate_id.startswith("configuration_"):
        refs = snapshot.model_configuration.evidence
    return PredicateResult(value, predicate_id, refs, snapshot.revision, explanation)


def _model_matches(target: ModelTarget, model: ModelState) -> bool:
    return (
        model.model_id == target.model_id
        and (target.provider is None or model.provider == target.provider)
        and (target.effort is None or model.effort == target.effort)
    )


def active_model_matches(target: ModelTarget, snapshot: ObservationSnapshot) -> PredicateResult:
    observed = snapshot.active_model
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "active_model_matches",
            TruthValue.UNKNOWN,
            f"active-model readback is {observed.knowledge.name.lower()}",
        )
    return _predicate(
        snapshot,
        "active_model_matches",
        TruthValue.TRUE if _model_matches(target, observed.value) else TruthValue.FALSE,
        "active model/provider/effort readback compared with target",
    )


def model_configuration_matches(
    target: ModelTarget, snapshot: ObservationSnapshot
) -> PredicateResult:
    """Compare observed staged configuration without equating it to activation."""
    observed = snapshot.model_configuration
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "configuration_matches",
            TruthValue.UNKNOWN,
            f"model configuration is {observed.knowledge.name.lower()}",
        )
    config: ModelConfigurationState = observed.value
    if config.configured_model_id != target.model_id:
        return _predicate(
            snapshot,
            "configuration_matches",
            TruthValue.FALSE,
            "configured model differs from target",
        )
    if config.pending_changes is not False:
        return _predicate(
            snapshot,
            "configuration_matches",
            TruthValue.UNKNOWN,
            "configured model has pending or unknown changes",
        )
    parameters = dict(config.parameters)
    missing = [name for name, expected in target.parameters() if parameters.get(name) != expected]
    if missing:
        return _predicate(
            snapshot,
            "configuration_matches",
            TruthValue.FALSE,
            f"configured parameter mismatch: {', '.join(missing)}",
        )
    return _predicate(
        snapshot,
        "configuration_matches",
        TruthValue.TRUE,
        "configured model and requested parameters match",
    )


def target_is_available(target: ModelTarget, snapshot: ObservationSnapshot) -> PredicateResult:
    """Fail only when a known picker explicitly excludes the target."""
    observed = snapshot.model_configuration
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "configuration_target_available",
            TruthValue.UNKNOWN,
            "model choices are not currently visible",
        )
    choices = observed.value.available
    if not choices:
        return _predicate(
            snapshot,
            "configuration_target_available",
            TruthValue.UNKNOWN,
            "configuration surface did not expose model choices",
        )
    for choice in choices:
        if target.model_id in (choice.stable_choice_id, choice.label):
            if choice.disabled is True:
                return _predicate(
                    snapshot,
                    "configuration_target_available",
                    TruthValue.FALSE,
                    "target model is visible but disabled",
                )
            return _predicate(
                snapshot,
                "configuration_target_available",
                TruthValue.TRUE,
                "target model is visible in the picker",
            )
    return _predicate(
        snapshot,
        "configuration_target_available",
        TruthValue.FALSE,
        "target model is absent from a known picker",
    )


def _after(snapshot: ObservationSnapshot, baseline: ObservationRevision | None) -> bool:
    return baseline is not None and snapshot.revision > baseline


def _configuration_parameters(snapshot: ObservationSnapshot) -> dict[str, object]:
    observed = snapshot.model_configuration
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return {}
    return dict(observed.value.parameters)


def _configured_model_is_target(op: SelectModelOperation, snapshot: ObservationSnapshot) -> bool:
    observed = snapshot.model_configuration
    return bool(
        observed.knowledge is Knowledge.PRESENT
        and observed.value is not None
        and observed.value.configured_model_id == op.request.target.model_id
    )


def _select_action(op: SelectModelOperation) -> SelectModel:
    target = op.request.target
    return SelectModel(
        action_id=str(uuid4()),
        operation_id=op.envelope.operation_id,
        duplicate_policy=DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
        model_id=target.model_id,
        provider=target.provider,
        effort=target.effort,
        context_mode=target.context_mode,
        fast_enabled=target.fast_enabled,
        max_mode_enabled=target.max_mode_enabled,
        thinking_enabled=target.thinking_enabled,
        run_mode=target.run_mode,
    )


def _open_picker_action(op: SelectModelOperation) -> OpenModelPicker:
    return OpenModelPicker(
        action_id=str(uuid4()),
        operation_id=op.envelope.operation_id,
        duplicate_policy=DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
    )


def _observe(
    phase: ModelSelectionPhase,
    reason: str,
    *predicates: PredicateResult,
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.OBSERVE_MORE, phase, None, reason, tuple(predicates)
    )


def _escalate(
    phase: ModelSelectionPhase,
    reason: str,
    *predicates: PredicateResult,
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE, phase, None, reason, tuple(predicates)
    )


def reconcile_model_selection(  # noqa: PLR0911, PLR0912, PLR0915 -- typed phase machine
    op: SelectModelOperation,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> ControllerDecision:
    """Reconcile a model target from current evidence; never replay confirmation.

    Runtime records the selected action id and the revision immediately before
    emission in the operation fields.  A restart therefore resumes from this
    state plus fresh observations, not from a procedural picker call stack.
    """
    phase = op.envelope.phase
    if snapshot.health.requires_escalation:
        return _escalate(ModelSelectionPhase.ESCALATED, "observation health requires escalation")
    if op.envelope.deadline is not None and now > op.envelope.deadline:
        if phase in {
            ModelSelectionPhase.AWAITING_CONFIGURATION,
            ModelSelectionPhase.AWAITING_PARAMETER_SELECTION,
            ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION,
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            ModelSelectionPhase.AMBIGUOUS,
        }:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "model confirmation emitted without verified convergence",
            )
        return ControllerDecision(
            ControllerDecisionKind.FAIL,
            ModelSelectionPhase.FAILED,
            None,
            "model-selection deadline exceeded before confirmation",
        )

    configured = model_configuration_matches(op.request.target, snapshot)
    active = active_model_matches(op.request.target, snapshot)

    if phase is ModelSelectionPhase.CREATED:
        if configured.value is TruthValue.TRUE and active.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                ModelSelectionPhase.SUCCEEDED,
                None,
                "target configuration already active",
                (configured, active),
            )
        return _observe(ModelSelectionPhase.ENSURING_CONFIGURATION, "begin model selection")

    if phase is ModelSelectionPhase.ENSURING_CONFIGURATION:
        if configured.value is TruthValue.TRUE:
            if active.value is TruthValue.TRUE:
                return ControllerDecision(
                    ControllerDecisionKind.SUCCEED,
                    ModelSelectionPhase.SUCCEEDED,
                    None,
                    "configured target has active-model readback",
                    (configured, active),
                )
            # A separately configured model is not evidence that it is active.
            return _observe(
                ModelSelectionPhase.REOPENING_PICKER,
                "configuration saved; reopen picker to activate it",
                configured,
                active,
            )
        availability = target_is_available(op.request.target, snapshot)
        if availability.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.FAIL,
                ModelSelectionPhase.FAILED,
                None,
                "target model is unavailable",
                (configured, availability),
            )
        if configured.value is TruthValue.UNKNOWN:
            if op.configuration_picker_action_id is not None:
                return _escalate(
                    ModelSelectionPhase.AMBIGUOUS,
                    "model picker was opened without visible configuration evidence",
                    configured,
                )
            if (
                snapshot.surface.knowledge is not Knowledge.PRESENT
                or snapshot.surface.value is None
            ):
                return _observe(phase, "wait for a known safe surface before opening model picker")
            if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
                return _escalate(
                    ModelSelectionPhase.ESCALATED,
                    "model picker is not visible and current surface is not safe to replace",
                    configured,
                )
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER,
                _open_picker_action(op),
                "open model picker before configuring target",
                (configured,),
            )
        if op.configuration_action_id is not None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "configuration action already exists without convergence",
                configured,
            )
        action = _select_action(op)
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            ModelSelectionPhase.AWAITING_CONFIGURATION,
            action,
            "configure requested model and parameters",
            (configured, availability),
        )

    if phase is ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER:
        if (
            op.configuration_picker_action_id is None
            or op.configuration_picker_baseline_revision is None
        ):
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "model-picker phase lacks persisted action identity or baseline revision",
            )
        if not _after(snapshot, op.configuration_picker_baseline_revision):
            return _observe(phase, "wait for fresh model-picker observation")
        if snapshot.model_configuration.knowledge is Knowledge.PRESENT:
            return _observe(
                ModelSelectionPhase.ENSURING_CONFIGURATION,
                "model picker observed; assess target configuration",
                configured,
            )
        return _escalate(
            ModelSelectionPhase.AMBIGUOUS,
            "model-picker request emitted without visible picker evidence",
            configured,
        )

    if phase is ModelSelectionPhase.AWAITING_CONFIGURATION:
        if op.configuration_action_id is None or op.configuration_baseline_revision is None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "configuration phase lacks persisted action identity or baseline revision",
                configured,
            )
        if not _after(snapshot, op.configuration_baseline_revision):
            return _observe(phase, "wait for a fresh configuration observation", configured)
        parameters = _configuration_parameters(snapshot)
        if parameters.get("stage") == "effort" and _configured_model_is_target(op, snapshot):
            if (
                op.request.target.effort is not None
                and parameters.get("effort") != op.request.target.effort
            ):
                return ControllerDecision(
                    ControllerDecisionKind.EMIT_ACTION,
                    ModelSelectionPhase.AWAITING_PARAMETER_SELECTION,
                    _select_action(op),
                    "select the requested reasoning effort on the observed parameter surface",
                    (configured,),
                )
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION,
                _select_action(op),
                "confirm the observed model parameter configuration",
                (configured,),
            )
        if configured.value is TruthValue.TRUE:
            if active.value is TruthValue.TRUE:
                return ControllerDecision(
                    ControllerDecisionKind.SUCCEED,
                    ModelSelectionPhase.SUCCEEDED,
                    None,
                    "configuration action converged and active readback matches",
                    (configured, active),
                )
            return _observe(
                ModelSelectionPhase.REOPENING_PICKER,
                "configuration converged; active model still needs separate selection",
                configured,
                active,
            )
        if configured.value is TruthValue.FALSE:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "fresh evidence contradicts configured target after confirmation",
                configured,
            )
        return _observe(phase, "await configuration acknowledgment", configured)

    if phase is ModelSelectionPhase.AWAITING_PARAMETER_SELECTION:
        if op.parameter_action_id is None or op.parameter_baseline_revision is None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "parameter phase lacks persisted action identity or baseline revision",
            )
        if not _after(snapshot, op.parameter_baseline_revision):
            return _observe(phase, "wait for fresh parameter-selection evidence")
        parameters = _configuration_parameters(snapshot)
        if (
            parameters.get("stage") == "effort"
            and _configured_model_is_target(op, snapshot)
            and parameters.get("effort") == op.request.target.effort
        ):
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION,
                _select_action(op),
                "selected effort is visible; confirm configuration explicitly",
            )
        return _escalate(
            ModelSelectionPhase.AMBIGUOUS,
            "fresh parameter evidence did not converge after selection",
        )

    if phase is ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION:
        if op.confirmation_action_id is None or op.confirmation_baseline_revision is None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "confirmation phase lacks persisted action identity or baseline revision",
            )
        if not _after(snapshot, op.confirmation_baseline_revision):
            return _observe(phase, "wait for fresh post-confirmation evidence")
        if active.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                ModelSelectionPhase.SUCCEEDED,
                None,
                "configuration confirmation also activated the requested model",
                (active,),
            )
        if (
            snapshot.surface.knowledge is Knowledge.PRESENT
            and snapshot.surface.value is not None
            and snapshot.surface.value.primary in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}
        ):
            return _observe(
                ModelSelectionPhase.REOPENING_PICKER,
                "configuration confirmation was acknowledged; verify separate activation",
                active,
            )
        return _observe(phase, "await configuration confirmation acknowledgment", active)

    if phase is ModelSelectionPhase.REOPENING_PICKER:
        if not op.configuration_acknowledged and configured.value is not TruthValue.TRUE:
            return _observe(
                ModelSelectionPhase.ENSURING_CONFIGURATION,
                "configuration is no longer established; reassess current picker state",
                configured,
            )
        if active.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                ModelSelectionPhase.SUCCEEDED,
                None,
                "active-model readback already matches target",
                (configured, active),
            )
        if op.activation_action_id is not None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "activation confirmation already exists without active readback",
                active,
            )
        if snapshot.model_configuration.knowledge is not Knowledge.PRESENT:
            if op.activation_picker_action_id is not None:
                return _escalate(
                    ModelSelectionPhase.AMBIGUOUS,
                    "activation picker was opened without visible picker evidence",
                    active,
                )
            if (
                snapshot.surface.knowledge is not Knowledge.PRESENT
                or snapshot.surface.value is None
            ):
                return _observe(phase, "wait for known safe surface before reopening model picker")
            if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
                return _escalate(
                    ModelSelectionPhase.ESCALATED,
                    "model picker is not visible and current surface is not safe to replace",
                    active,
                )
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION,
                ModelSelectionPhase.AWAITING_ACTIVATION_PICKER,
                _open_picker_action(op),
                "reopen model picker before activating configured target",
                (configured, active),
            )
        action = _select_action(op)
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            action,
            "reopen picker and activate configured target",
            (configured, active),
        )

    if phase is ModelSelectionPhase.AWAITING_ACTIVATION_PICKER:
        if op.activation_picker_action_id is None or op.activation_picker_baseline_revision is None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "activation picker phase lacks persisted action identity or baseline revision",
            )
        if not _after(snapshot, op.activation_picker_baseline_revision):
            return _observe(phase, "wait for fresh reopened model-picker observation")
        if snapshot.model_configuration.knowledge is Knowledge.PRESENT:
            return _observe(
                ModelSelectionPhase.REOPENING_PICKER,
                "reopened model picker observed; activate configured target",
                configured,
                active,
            )
        return _escalate(
            ModelSelectionPhase.AMBIGUOUS,
            "activation picker request emitted without visible picker evidence",
            active,
        )

    if phase is ModelSelectionPhase.AWAITING_ACTIVE_READBACK:
        if op.activation_action_id is None or op.activation_baseline_revision is None:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "activation phase lacks persisted action identity or baseline revision",
                active,
            )
        if not _after(snapshot, op.activation_baseline_revision):
            return _observe(phase, "wait for fresh post-selection active-model readback", active)
        if active.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                ModelSelectionPhase.SUCCEEDED,
                None,
                "fresh active-model readback confirms target activation",
                (configured, active),
            )
        if active.value is TruthValue.FALSE:
            return _escalate(
                ModelSelectionPhase.AMBIGUOUS,
                "fresh active-model readback contradicts selection; confirmation is not replayable",
                active,
            )
        return _observe(phase, "await active-model readback", active)

    if phase is ModelSelectionPhase.AMBIGUOUS:
        return _escalate(
            ModelSelectionPhase.ESCALATED,
            op.ambiguity_reason or "ambiguous model configuration or activation",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL,
        ModelSelectionPhase.FAILED,
        None,
        f"invalid model-selection phase {phase.name}",
    )


def advance_model_selection(
    op: SelectModelOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> SelectModelOperation:
    """Persist configuration/activation intent before lowering it to effects."""

    status = op.envelope.status
    if decision.kind is ControllerDecisionKind.SUCCEED:
        status = OperationStatus.SUCCEEDED
    elif decision.kind is ControllerDecisionKind.FAIL:
        status = OperationStatus.FAILED
    elif decision.kind is ControllerDecisionKind.ESCALATE:
        status = OperationStatus.ESCALATED
    elif status is OperationStatus.PENDING:
        status = OperationStatus.RUNNING
    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, ModelSelectionPhase)
        else op.envelope.phase
    )
    action_history = op.envelope.action_history
    config_action_id = op.configuration_action_id
    activate_action_id = op.activation_action_id
    config_picker_action_id = op.configuration_picker_action_id
    parameter_action_id = op.parameter_action_id
    confirmation_action_id = op.confirmation_action_id
    activate_picker_action_id = op.activation_picker_action_id
    config_baseline = op.configuration_baseline_revision
    activate_baseline = op.activation_baseline_revision
    config_picker_baseline = op.configuration_picker_baseline_revision
    parameter_baseline = op.parameter_baseline_revision
    confirmation_baseline = op.confirmation_baseline_revision
    activate_picker_baseline = op.activation_picker_baseline_revision
    ambiguity = op.ambiguity_reason
    if decision.action is not None:
        action_history = (*action_history, decision.action.action_id)
        if phase is ModelSelectionPhase.AWAITING_CONFIGURATION:
            config_action_id = decision.action.action_id
            config_baseline = snapshot.revision
        elif phase is ModelSelectionPhase.AWAITING_PARAMETER_SELECTION:
            parameter_action_id = decision.action.action_id
            parameter_baseline = snapshot.revision
        elif phase is ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION:
            confirmation_action_id = decision.action.action_id
            confirmation_baseline = snapshot.revision
        elif phase is ModelSelectionPhase.AWAITING_ACTIVE_READBACK:
            activate_action_id = decision.action.action_id
            activate_baseline = snapshot.revision
        elif phase is ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER:
            config_picker_action_id = decision.action.action_id
            config_picker_baseline = snapshot.revision
        elif phase is ModelSelectionPhase.AWAITING_ACTIVATION_PICKER:
            activate_picker_action_id = decision.action.action_id
            activate_picker_baseline = snapshot.revision
    if decision.kind is ControllerDecisionKind.ESCALATE:
        ambiguity = decision.reason
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
        configuration_action_id=config_action_id,
        activation_action_id=activate_action_id,
        configuration_picker_action_id=config_picker_action_id,
        parameter_action_id=parameter_action_id,
        confirmation_action_id=confirmation_action_id,
        activation_picker_action_id=activate_picker_action_id,
        configuration_baseline_revision=config_baseline,
        activation_baseline_revision=activate_baseline,
        configuration_picker_baseline_revision=config_picker_baseline,
        parameter_baseline_revision=parameter_baseline,
        confirmation_baseline_revision=confirmation_baseline,
        configuration_acknowledged=(
            op.configuration_acknowledged
            or (
                op.envelope.phase is ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION
                and phase is ModelSelectionPhase.REOPENING_PICKER
            )
        ),
        activation_picker_baseline_revision=activate_picker_baseline,
        ambiguity_reason=ambiguity,
    )


__all__ = [
    "ModelSelectionOutcome",
    "ModelSelectionPhase",
    "ModelTarget",
    "SelectModelOperation",
    "SelectModelRequest",
    "SelectModelResult",
    "active_model_matches",
    "advance_model_selection",
    "model_configuration_matches",
    "reconcile_model_selection",
    "target_is_available",
]
