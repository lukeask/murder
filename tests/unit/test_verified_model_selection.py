"""Trace tests for verified staged model configuration and activation."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.model_selection import (
    ModelSelectionPhase,
    ModelTarget,
    SelectModelOperation,
    SelectModelRequest,
    advance_model_selection,
    reconcile_model_selection,
)
from murder.llm.harness_control.model.actions import (
    DismissOverlay,
    DuplicatePolicy,
    OpenModelPicker,
    SelectModel,
)
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    ComposerActionability,
    ComposerState,
    Knowledge,
    ModelConfigurationState,
    ModelState,
    ObservationRevision,
    Observed,
    SurfaceKind,
    SurfaceState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)

NOW = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
TARGET = ModelTarget(
    model_id="gpt-5.5",
    provider="openai",
    effort="high",
    fast_enabled=True,
)


def _revision(sequence: int) -> ObservationRevision:
    return ObservationRevision(0, sequence, sequence)


def _observed(value, revision: ObservationRevision):
    return Observed.present(value, evidence=(), observed_at=NOW, revision=revision)


def _snapshot(
    sequence: int,
    *,
    active: ModelState | None = None,
    configuration: ModelConfigurationState | None = None,
):
    revision = _revision(sequence)
    snapshot = unknown_snapshot("codex", captured_at=NOW, revision=revision)
    if active is not None:
        snapshot = replace(snapshot, active_model=_observed(active, revision))
    if configuration is not None:
        snapshot = replace(snapshot, model_configuration=_observed(configuration, revision))
    return snapshot


def _configuration(
    *,
    model_id: str = "other-model",
    pending: bool | None = False,
    available: tuple[ChoiceState, ...] | None = None,
    effort: str = "high",
    fast: bool = True,
) -> ModelConfigurationState:
    return ModelConfigurationState(
        available=available if available is not None else (ChoiceState("gpt-5.5", "GPT-5.5"),),
        highlighted_model_id=model_id,
        selected_model_id=model_id,
        configured_model_id=model_id,
        pending_changes=pending,
        parameters=(("effort", effort), ("fast_enabled", fast)),
    )


def _operation(
    phase: ModelSelectionPhase,
    *,
    configuration_action_id: str | None = None,
    activation_action_id: str | None = None,
    configuration_baseline: ObservationRevision | None = None,
    activation_baseline: ObservationRevision | None = None,
    parameter_action_id: str | None = None,
    confirmation_action_id: str | None = None,
    parameter_baseline: ObservationRevision | None = None,
    confirmation_baseline: ObservationRevision | None = None,
    deadline: datetime | None = None,
) -> SelectModelOperation:
    return SelectModelOperation(
        envelope=OperationEnvelope(
            operation_id="model-op",
            capability="select_model",
            status=OperationStatus.RUNNING,
            phase=phase,
            created_at=NOW,
            updated_at=NOW,
            deadline=deadline,
        ),
        request=SelectModelRequest(TARGET, timedelta(minutes=3)),
        configuration_action_id=configuration_action_id,
        activation_action_id=activation_action_id,
        parameter_action_id=parameter_action_id,
        confirmation_action_id=confirmation_action_id,
        configuration_baseline_revision=configuration_baseline,
        activation_baseline_revision=activation_baseline,
        parameter_baseline_revision=parameter_baseline,
        confirmation_baseline_revision=confirmation_baseline,
    )


def test_configure_then_reopen_picker_then_verify_active_readback() -> None:
    initial = _snapshot(
        1,
        active=ModelState("other-model", "medium", "Other", "openai"),
        configuration=_configuration(),
    )
    configure = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), initial, NOW
    )
    assert configure.kind is ControllerDecisionKind.EMIT_ACTION
    assert configure.next_phase is ModelSelectionPhase.AWAITING_CONFIGURATION
    assert isinstance(configure.action, SelectModel)
    assert configure.action.duplicate_policy is DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION

    saved = _snapshot(
        2,
        active=ModelState("other-model", "medium", "Other", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    configured = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_CONFIGURATION,
            configuration_action_id="configure-1",
            configuration_baseline=_revision(1),
        ),
        saved,
        NOW,
    )
    assert configured.kind is ControllerDecisionKind.OBSERVE_MORE
    assert configured.next_phase is ModelSelectionPhase.REOPENING_PICKER

    activate = reconcile_model_selection(
        _operation(ModelSelectionPhase.REOPENING_PICKER), saved, NOW
    )
    assert activate.kind is ControllerDecisionKind.EMIT_ACTION
    assert activate.next_phase is ModelSelectionPhase.AWAITING_ACTIVE_READBACK
    assert isinstance(activate.action, SelectModel)

    stale_readback = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            activation_action_id="activate-1",
            activation_baseline=_revision(2),
        ),
        saved,
        NOW,
    )
    assert stale_readback.kind is ControllerDecisionKind.OBSERVE_MORE
    assert stale_readback.action is None

    active = _snapshot(
        3,
        active=ModelState("gpt-5.5", "high", "GPT-5.5", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    complete = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            activation_action_id="activate-1",
            activation_baseline=_revision(2),
        ),
        active,
        NOW,
    )
    assert complete.kind is ControllerDecisionKind.SUCCEED
    assert complete.next_phase is ModelSelectionPhase.SUCCEEDED

    effort_medium = _snapshot(
        4,
        active=ModelState("other-model", "medium", "Other", "openai"),
        configuration=ModelConfigurationState(
            (),
            None,
            "gpt-5.5",
            "gpt-5.5",
            True,
            (("stage", "effort"), ("effort", "medium"), ("effort_option.high", "3")),
        ),
    )
    select_effort = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_CONFIGURATION,
            configuration_action_id="configure-stage",
            configuration_baseline=_revision(3),
        ),
        effort_medium,
        NOW,
    )
    assert select_effort.kind is ControllerDecisionKind.EMIT_ACTION
    assert select_effort.next_phase is ModelSelectionPhase.AWAITING_PARAMETER_SELECTION

    effort_high = replace(
        effort_medium,
        revision=_revision(5),
        model_configuration=_observed(
            replace(
                effort_medium.model_configuration.value,
                parameters=(("stage", "effort"), ("effort", "high")),
            ),
            _revision(5),
        ),
    )
    confirm = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_PARAMETER_SELECTION,
            parameter_action_id="effort-action",
            parameter_baseline=_revision(4),
        ),
        effort_high,
        NOW,
    )
    assert confirm.kind is ControllerDecisionKind.EMIT_ACTION
    assert confirm.next_phase is ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION
    assert isinstance(confirm.action, DismissOverlay)

    acknowledged = replace(
        _snapshot(6, active=ModelState("other-model", "medium", "Other", "openai")),
        surface=_observed(
            SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            ),
            _revision(6),
        ),
    )
    reopen = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION,
            confirmation_action_id="confirm-action",
            confirmation_baseline=_revision(5),
        ),
        acknowledged,
        NOW,
    )
    assert reopen.kind is ControllerDecisionKind.OBSERVE_MORE
    assert reopen.next_phase is ModelSelectionPhase.REOPENING_PICKER


def test_saved_parameters_return_to_picker_then_activate_without_reopening_editor() -> None:
    revision = _revision(2)
    snapshot = _snapshot(
        2,
        active=ModelState("other-model", "medium", "Other", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    snapshot = replace(
        snapshot,
        surface=_observed(
            SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            ),
            revision,
        ),
    )

    decision = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_CONFIGURATION_CONFIRMATION,
            confirmation_action_id="dismiss-parameters",
            confirmation_baseline=_revision(1),
        ),
        snapshot,
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert decision.next_phase is ModelSelectionPhase.AWAITING_ACTIVE_READBACK
    assert isinstance(decision.action, SelectModel)
    assert decision.action.effort is None


def test_unobserved_picker_requires_a_distinct_safe_open_action_before_selection() -> None:
    snapshot = _snapshot(1, active=ModelState("other", "medium", "Other", "openai"))
    snapshot = replace(
        snapshot,
        surface=_observed(
            SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            ),
            _revision(1),
        ),
    )
    decision = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), snapshot, NOW
    )
    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert decision.next_phase is ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER
    assert isinstance(decision.action, OpenModelPicker)
    assert decision.action.duplicate_policy is DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS


def test_fresh_active_readback_completes_direct_selection_without_config_surface() -> None:
    snapshot = _snapshot(
        2,
        active=ModelState("gpt-5.5", "high", "GPT-5.5", "openai"),
    )

    decision = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_CONFIGURATION,
            configuration_action_id="select-row",
            configuration_baseline=_revision(1),
        ),
        snapshot,
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.SUCCEED
    assert decision.next_phase is ModelSelectionPhase.SUCCEEDED


def test_cursor_pending_model_slash_command_is_confirmed_before_escalation() -> None:
    revision = _revision(2)
    snapshot = _snapshot(2)
    snapshot = replace(
        snapshot,
        surface=_observed(
            SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            ),
            revision,
        ),
        composer=_observed(
            ComposerState(
                "/model [filter] Select model",
                "/model [filter] Select model",
                "pending-model-command",
                True,
                True,
                ComposerActionability.ACTIONABLE,
                False,
                True,
            ),
            revision,
        ),
    )
    operation = replace(
        _operation(ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER),
        configuration_picker_action_id="open-model-picker",
        configuration_picker_baseline_revision=_revision(1),
    )

    decision = reconcile_model_selection(operation, snapshot, NOW)

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert decision.next_phase is ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER
    assert isinstance(decision.action, OpenModelPicker)


def test_targeted_model_command_can_converge_directly_from_fresh_active_readback() -> None:
    snapshot = _snapshot(
        2,
        active=ModelState("gpt-5-5", "high", "GPT-5.5", "openai"),
    )
    operation = replace(
        _operation(ModelSelectionPhase.AWAITING_CONFIGURATION_PICKER),
        configuration_picker_action_id="targeted-model-command",
        configuration_picker_baseline_revision=_revision(1),
    )

    decision = reconcile_model_selection(operation, snapshot, NOW)

    assert decision.kind is ControllerDecisionKind.SUCCEED
    assert decision.next_phase is ModelSelectionPhase.SUCCEEDED


def test_configured_or_selected_picker_row_never_proves_activation() -> None:
    snapshot = _snapshot(
        1,
        active=ModelState("other-model", "high", "Other", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    decision = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), snapshot, NOW
    )
    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.next_phase is ModelSelectionPhase.REOPENING_PICKER
    assert decision.action is None


def test_matching_active_readback_does_not_leave_open_picker_blocking_input() -> None:
    revision = _revision(1)
    snapshot = _snapshot(
        1,
        active=ModelState("gpt-5.5", "high", "GPT-5.5", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    snapshot = replace(
        snapshot,
        surface=_observed(
            SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            ),
            revision,
        ),
    )

    decision = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), snapshot, NOW
    )

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert decision.next_phase is ModelSelectionPhase.AWAITING_CONFIGURATION
    assert isinstance(decision.action, SelectModel)


def test_fresh_negative_active_readback_escalates_without_replaying_confirmation() -> None:
    snapshot = _snapshot(
        3,
        active=ModelState("other-model", "high", "Other", "openai"),
        configuration=_configuration(model_id="gpt-5.5"),
    )
    decision = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            activation_action_id="activate-1",
            activation_baseline=_revision(2),
        ),
        snapshot,
        NOW,
    )
    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.next_phase is ModelSelectionPhase.AMBIGUOUS
    assert decision.action is None


def test_disabled_target_fails_before_any_model_confirmation() -> None:
    snapshot = _snapshot(
        1,
        configuration=_configuration(
            available=(ChoiceState("gpt-5.5", "GPT-5.5", disabled=True),),
        ),
    )
    decision = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), snapshot, NOW
    )
    assert decision.kind is ControllerDecisionKind.FAIL
    assert decision.next_phase is ModelSelectionPhase.FAILED
    assert decision.action is None


def test_post_confirmation_timeout_escalates_instead_of_replaying_selection() -> None:
    snapshot = _snapshot(3, configuration=_configuration(model_id="gpt-5.5"))
    decision = reconcile_model_selection(
        _operation(
            ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
            activation_action_id="activate-1",
            activation_baseline=_revision(2),
            deadline=NOW - timedelta(seconds=1),
        ),
        snapshot,
        NOW,
    )
    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.next_phase is ModelSelectionPhase.AMBIGUOUS
    assert decision.action is None


def test_unknown_configuration_is_not_treated_as_absent_or_safe_to_select() -> None:
    snapshot = unknown_snapshot("codex", captured_at=NOW, revision=_revision(1))
    assert snapshot.model_configuration.knowledge is Knowledge.UNKNOWN
    decision = reconcile_model_selection(
        _operation(ModelSelectionPhase.ENSURING_CONFIGURATION), snapshot, NOW
    )
    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.action is None


def test_configuration_action_is_durable_before_picker_effect() -> None:
    snapshot = _snapshot(1, configuration=_configuration())
    op = _operation(ModelSelectionPhase.ENSURING_CONFIGURATION)
    decision = reconcile_model_selection(op, snapshot, NOW)
    advanced = advance_model_selection(op, decision, snapshot, NOW)
    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert advanced.configuration_action_id == decision.action.action_id
    assert advanced.configuration_baseline_revision == snapshot.revision
