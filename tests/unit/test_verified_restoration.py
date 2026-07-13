from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.restoration import (
    InterruptOperation,
    InterruptPhase,
    InterruptRequest,
    RestorationPhase,
    RestoreComposerOperation,
    RestoreComposerRequest,
    advance_interrupt,
    advance_restore_composer,
    reconcile_interrupt,
    reconcile_restore_composer,
)
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    ComposerState,
    GenerationPhase,
    GenerationState,
    ObservationRevision,
    Observed,
    SurfaceKind,
    SurfaceState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import OperationEnvelope, OperationStatus

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_restore_dismisses_current_observed_overlay():
    s = unknown_snapshot(HarnessId("codex"), captured_at=NOW, revision=ObservationRevision(0, 1, 1))
    r = s.revision
    s = replace(
        s,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.USAGE_PANEL,
                frozenset({SurfaceKind.USAGE_PANEL}),
                SurfaceKind.USAGE_PANEL,
                True,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=r,
        ),
    )
    op = RestoreComposerOperation(
        OperationEnvelope(
            "r",
            "restore",
            OperationStatus.RUNNING,
            RestorationPhase.CREATED,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        RestoreComposerRequest(timedelta(minutes=1)),
    )
    decision = reconcile_restore_composer(op, s, NOW)
    assert decision.kind.name == "EMIT_ACTION"
    assert (
        advance_restore_composer(op, decision, s, NOW).envelope.status
        is OperationStatus.RUNNING
    )

    actionable = replace(
        s,
        composer=Observed.present(
            ComposerState(
                "", "", "empty", True, True, ComposerActionability.ACTIONABLE, False, True
            ),
            evidence=(),
            observed_at=NOW,
            revision=r,
        ),
    )
    succeeded = reconcile_restore_composer(op, actionable, NOW)
    assert succeeded.kind.name == "SUCCEED"
    assert (
        advance_restore_composer(op, succeeded, actionable, NOW).envelope.status
        is OperationStatus.SUCCEEDED
    )


def test_restore_waits_when_dismissal_has_no_fresh_observation():
    s = unknown_snapshot(HarnessId("codex"), captured_at=NOW, revision=ObservationRevision(0, 1, 1))
    r = s.revision
    s = replace(
        s,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.USAGE_PANEL,
                frozenset({SurfaceKind.USAGE_PANEL}),
                SurfaceKind.USAGE_PANEL,
                True,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=r,
        ),
    )
    op = RestoreComposerOperation(
        OperationEnvelope(
            "r",
            "restore",
            OperationStatus.RUNNING,
            RestorationPhase.AWAITING_SURFACE,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        RestoreComposerRequest(timedelta(minutes=1)),
        r,
        "dismiss",
    )
    assert reconcile_restore_composer(op, s, NOW).kind.name == "OBSERVE_MORE"


def test_interrupt_does_not_treat_unknown_generation_as_already_stopped():
    snapshot = unknown_snapshot(
        HarnessId("codex"), captured_at=NOW, revision=ObservationRevision(0, 1, 1)
    )
    operation = InterruptOperation(
        OperationEnvelope(
            "interrupt",
            "interrupt",
            OperationStatus.RUNNING,
            InterruptPhase.CREATED,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        InterruptRequest(timedelta(minutes=1)),
    )
    decision = reconcile_interrupt(operation, snapshot, NOW)
    assert decision.kind.name == "OBSERVE_MORE"
    assert decision.action is None

    emitted = replace(
        operation,
        envelope=replace(
            operation.envelope,
            phase=InterruptPhase.AWAITING_ACKNOWLEDGMENT,
            action_history=("interrupt-action",),
        ),
        baseline_revision=ObservationRevision(0, 0, 1),
        action_id="interrupt-action",
    )
    after_unknown = advance_interrupt(
        emitted, reconcile_interrupt(emitted, snapshot, NOW), snapshot, NOW
    )
    assert after_unknown.envelope.phase is InterruptPhase.AWAITING_ACKNOWLEDGMENT

    active_revision = ObservationRevision(0, 2, 2)
    active = replace(
        snapshot,
        revision=active_revision,
        generation=Observed.present(
            GenerationState(GenerationPhase.STREAMING, True, True, None, None, None),
            evidence=(),
            observed_at=NOW,
            revision=active_revision,
        ),
    )
    retry = reconcile_interrupt(after_unknown, active, NOW)
    assert retry.kind.name == "OBSERVE_MORE"
    assert retry.action is None
