from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.model_discovery import (
    DiscoverModelsOperation,
    DiscoverModelsRequest,
    ModelDiscoveryPhase,
    advance_model_discovery,
    reconcile_model_discovery,
)
from murder.llm.harness_control.capabilities.session_settings import (
    ConfigureSessionSettingsOperation,
    SessionSettingsPhase,
    SessionSettingsTarget,
    advance_session_settings,
    reconcile_session_settings,
)
from murder.llm.harness_control.model.actions import (
    ConfigureSessionSettings,
    NavigateModelPicker,
)
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    ModelConfigurationState,
    ObservationRevision,
    Observed,
    SessionSettingsState,
    SurfaceKind,
    SurfaceState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)

NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)
GPT_OSS_INDEX = 2


def _observed(value, sequence: int):
    revision = ObservationRevision(0, sequence, sequence)
    return Observed.present(value, evidence=(), observed_at=NOW, revision=revision)


def _picker(sequence: int, highlighted: int):
    revision = ObservationRevision(0, sequence, sequence)
    choices = (
        ChoiceState("gemini", "Gemini (Low)", highlighted=highlighted == 0),
        ChoiceState("gemini", "Gemini (High)", highlighted=highlighted == 1),
        ChoiceState(
            "gpt-oss", "GPT-OSS (Medium)", highlighted=highlighted == GPT_OSS_INDEX
        ),
    )
    snapshot = unknown_snapshot("antigravity", captured_at=NOW, revision=revision)
    return replace(
        snapshot,
        surface=_observed(
            SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            ),
            sequence,
        ),
        model_configuration=_observed(
            ModelConfigurationState(choices, "gemini", None, "gpt-oss", False), sequence
        ),
    )


def test_model_discovery_traverses_duplicate_model_effort_variants_before_wrapping() -> None:
    operation = DiscoverModelsOperation(
        OperationEnvelope(
            "discover",
            "discover_models",
            OperationStatus.RUNNING,
            ModelDiscoveryPhase.SCANNING,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        DiscoverModelsRequest(timedelta(minutes=1)),
    )
    first = _picker(1, 0)
    decision = reconcile_model_discovery(operation, first, NOW)
    assert isinstance(decision.action, NavigateModelPicker)
    operation = advance_model_discovery(operation, decision, first, NOW)
    assert operation.start_model_id == "gemini\x1fGemini (Low)"

    second = _picker(2, 1)
    decision = reconcile_model_discovery(operation, second, NOW)
    assert decision.next_phase is ModelDiscoveryPhase.SCANNING
    operation = advance_model_discovery(operation, decision, second, NOW)
    decision = reconcile_model_discovery(operation, second, NOW)
    assert isinstance(decision.action, NavigateModelPicker)


def test_session_settings_emit_once_then_require_fresh_matching_readback() -> None:
    revision = ObservationRevision(0, 1, 1)
    snapshot = unknown_snapshot("codex", captured_at=NOW, revision=revision)
    snapshot = replace(
        snapshot,
        settings=_observed(SessionSettingsState("default", False), 1),
    )
    operation = ConfigureSessionSettingsOperation(
        OperationEnvelope(
            "settings",
            "configure_session_settings",
            OperationStatus.RUNNING,
            SessionSettingsPhase.CREATED,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        SessionSettingsTarget(run_mode="plan", fast_enabled=True),
    )
    decision = reconcile_session_settings(operation, snapshot, NOW)
    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert isinstance(decision.action, ConfigureSessionSettings)
    operation = advance_session_settings(operation, decision, snapshot, NOW)

    fresh = replace(
        snapshot,
        revision=ObservationRevision(0, 2, 2),
        settings=_observed(SessionSettingsState("plan", True), 2),
    )
    decision = reconcile_session_settings(operation, fresh, NOW)
    assert decision.kind is ControllerDecisionKind.SUCCEED
