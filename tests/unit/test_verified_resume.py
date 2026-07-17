from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.resume import (
    ConfigureResumeOperation,
    ConfigureResumePhase,
    OpenResumeOperation,
    OpenResumePhase,
    OpenResumeRequest,
    ResumePickerTarget,
    advance_configure_resume,
    advance_open_resume,
    reconcile_configure_resume,
    reconcile_open_resume,
)
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    ComposerState,
    ObservationRevision,
    Observed,
    QuestionState,
    SurfaceKind,
    SurfaceState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import OperationEnvelope, OperationStatus

NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _operation(phase: OpenResumePhase = OpenResumePhase.CREATED) -> OpenResumeOperation:
    return OpenResumeOperation(
        OperationEnvelope(
            "resume-op",
            "open_resume_picker",
            OperationStatus.PENDING,
            phase,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        OpenResumeRequest(timedelta(minutes=1)),
    )


def test_open_resume_requires_actionable_composer_then_fresh_picker() -> None:
    snapshot = unknown_snapshot(
        HarnessId("codex"),
        captured_at=NOW,
        revision=ObservationRevision(0, 1, 1),
    )
    revision = snapshot.revision
    snapshot = replace(
        snapshot,
        composer=Observed.present(
            ComposerState(
                "",
                "",
                "empty",
                True,
                True,
                ComposerActionability.ACTIONABLE,
                False,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=revision,
        ),
    )
    awaiting = advance_open_resume(
        _operation(), reconcile_open_resume(_operation(), snapshot, NOW), snapshot, NOW
    )
    decision = reconcile_open_resume(awaiting, snapshot, NOW)
    assert decision.kind.name == "EMIT_ACTION"
    assert decision.action is not None

    emitted = advance_open_resume(awaiting, decision, snapshot, NOW)
    same_frame = reconcile_open_resume(emitted, snapshot, NOW)
    assert same_frame.kind.name == "OBSERVE_MORE"

    fresh_revision = ObservationRevision(0, 2, 2)
    picker = replace(
        snapshot,
        revision=fresh_revision,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.RESUME_PICKER,
                frozenset({SurfaceKind.RESUME_PICKER}),
                SurfaceKind.RESUME_PICKER,
                True,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=fresh_revision,
        ),
    )
    succeeded = reconcile_open_resume(emitted, picker, NOW)
    assert succeeded.kind.name == "SUCCEED"
    assert (
        advance_open_resume(emitted, succeeded, picker, NOW).envelope.status
        is OperationStatus.SUCCEEDED
    )


def test_resume_configuration_requires_fresh_matching_readback() -> None:
    revision = ObservationRevision(0, 3, 3)
    snapshot = unknown_snapshot(HarnessId("codex"), captured_at=NOW, revision=revision)
    snapshot = replace(
        snapshot,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.RESUME_PICKER,
                frozenset({SurfaceKind.RESUME_PICKER}),
                SurfaceKind.RESUME_PICKER,
                True,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=revision,
        ),
        question=Observed.present(
            QuestionState(
                "resume",
                "Resume a previous session",
                (),
                "single",
                "filter=Cwd;sort=Updated",
                ("filter=Cwd", "filter=All", "sort=Updated", "sort=Created"),
                False,
                "",
                "Enter",
                "Escape",
                (),
            ),
            evidence=(),
            observed_at=NOW,
            revision=revision,
        ),
    )
    target = ResumePickerTarget("needle", "all", "created")
    operation = ConfigureResumeOperation(
        OperationEnvelope(
            "configure-resume",
            "configure_resume_picker",
            OperationStatus.PENDING,
            ConfigureResumePhase.READY,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        target,
    )
    emitted_decision = reconcile_configure_resume(operation, snapshot, NOW)
    assert emitted_decision.kind.name == "EMIT_ACTION"
    emitted = advance_configure_resume(operation, emitted_decision, snapshot, NOW)

    fresh_revision = ObservationRevision(0, 4, 4)
    configured = replace(
        snapshot,
        revision=fresh_revision,
        question=Observed.present(
            replace(
                snapshot.question.value,
                active_tab="filter=All;sort=Created",
                custom_answer_text="needle",
            ),
            evidence=(),
            observed_at=NOW,
            revision=fresh_revision,
        ),
    )
    succeeded = reconcile_configure_resume(emitted, configured, NOW)
    assert succeeded.kind.name == "SUCCEED"
