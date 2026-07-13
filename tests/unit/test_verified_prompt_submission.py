from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.submit_prompt import (
    advance_submit_prompt,
    reconcile_submit_prompt,
)
from murder.llm.harness_control.model import (
    ComposerActionability,
    ComposerState,
    ControllerDecisionKind,
    HarnessId,
    InputChunk,
    InputProvenance,
    ObservationRevision,
    Observed,
    OperationEnvelope,
    OperationStatus,
    PromptPayload,
    SubmitPhase,
    SubmitPromptOperation,
    SubmitPromptRequest,
    SurfaceKind,
    SurfaceState,
    TranscriptTailState,
    TurnRef,
    unknown_snapshot,
)


def _operation(
    phase: SubmitPhase, *, commit: str | None = None, await_completion: bool = False
) -> SubmitPromptOperation:
    now = datetime.now(timezone.utc)
    payload = PromptPayload(
        (InputChunk("hello", InputProvenance.USER_TYPED, "chunk-1"),), "hello", "fingerprint"
    )
    envelope = OperationEnvelope(
        "operation-1",
        "submit_prompt",
        OperationStatus.RUNNING,
        phase,
        now,
        now,
        now + timedelta(minutes=1),
    )
    return SubmitPromptOperation(
        envelope,
        SubmitPromptRequest(payload, await_completion, timedelta(minutes=1)),
        "fingerprint",
        insertion_revision=ObservationRevision(0, 0, 0),
        commit_action_id=commit,
        baseline_revision=ObservationRevision(0, 1, 1),
    )


def _ready_snapshot():
    now = datetime.now(timezone.utc)
    snap = unknown_snapshot(
        HarnessId("codex"), captured_at=now, revision=ObservationRevision(0, 1, 1)
    )
    surface = SurfaceState(
        SurfaceKind.COMPOSER, frozenset({SurfaceKind.COMPOSER}), SurfaceKind.COMPOSER, False, False
    )
    composer = ComposerState(
        "hello", "hello", "fingerprint", True, True, ComposerActionability.ACTIONABLE, False, True
    )
    return replace(
        snap,
        surface=Observed.present(surface, evidence=(), observed_at=now, revision=snap.revision),
        composer=Observed.present(composer, evidence=(), observed_at=now, revision=snap.revision),
    )


def test_verified_payload_must_be_seen_before_commit() -> None:
    snapshot = _ready_snapshot()
    decision = reconcile_submit_prompt(
        _operation(SubmitPhase.VERIFYING_PAYLOAD), snapshot, datetime.now(timezone.utc)
    )
    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.next_phase is SubmitPhase.READY_TO_COMMIT


def test_ambiguous_post_enter_never_replays_enter() -> None:
    snapshot = replace(_ready_snapshot(), revision=ObservationRevision(0, 2, 2))
    decision = reconcile_submit_prompt(
        _operation(SubmitPhase.AWAITING_ACKNOWLEDGMENT, commit="commit-1"),
        snapshot,
        datetime.now(timezone.utc),
    )
    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_insert_action_sets_freshness_boundary_before_emission() -> None:
    snapshot = _ready_snapshot()
    op = _operation(SubmitPhase.INSERTING_PAYLOAD)
    decision = reconcile_submit_prompt(op, snapshot, datetime.now(timezone.utc))
    advanced = advance_submit_prompt(op, decision, snapshot, datetime.now(timezone.utc))
    assert advanced.insertion_action_id == decision.action.action_id
    assert advanced.insertion_revision == snapshot.revision


def test_payload_cannot_be_verified_from_pre_insert_capture() -> None:
    snapshot = _ready_snapshot()
    op = _operation(SubmitPhase.VERIFYING_PAYLOAD)
    op = replace(op, insertion_revision=snapshot.revision)
    decision = reconcile_submit_prompt(op, snapshot, datetime.now(timezone.utc))
    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.next_phase is SubmitPhase.VERIFYING_PAYLOAD


def test_empty_composer_uses_text_not_empty_hash_sentinel() -> None:
    snapshot = _ready_snapshot()
    empty = ComposerState(
        "",
        "",
        "sha256-of-empty",
        True,
        True,
        ComposerActionability.ACTIONABLE,
        False,
        True,
    )
    snapshot = replace(
        snapshot,
        composer=Observed.present(
            empty,
            evidence=(),
            observed_at=snapshot.captured_at,
            revision=snapshot.revision,
        ),
    )
    decision = reconcile_submit_prompt(
        _operation(SubmitPhase.CLEARING_COMPOSER), snapshot, datetime.now(timezone.utc)
    )
    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.next_phase is SubmitPhase.INSERTING_PAYLOAD


def test_completion_waits_for_a_completed_assistant_turn_after_submission_acknowledgment() -> None:
    snapshot = replace(_ready_snapshot(), revision=ObservationRevision(0, 2, 2))
    acknowledged_tail = TranscriptTailState(
        TurnRef("user-1", "user"),
        TurnRef("assistant-1", "assistant"),
        ("fingerprint",),
        True,
        False,
        "streaming",
        2,
    )
    snapshot = replace(
        snapshot,
        transcript_tail=Observed.present(
            acknowledged_tail,
            evidence=(),
            observed_at=snapshot.captured_at,
            revision=snapshot.revision,
        ),
    )
    op = replace(
        _operation(
            SubmitPhase.AWAITING_ACKNOWLEDGMENT,
            commit="commit-1",
            await_completion=True,
        ),
        baseline_transcript_revision=1,
    )
    acknowledged = reconcile_submit_prompt(op, snapshot, datetime.now(timezone.utc))
    assert acknowledged.kind is ControllerDecisionKind.OBSERVE_MORE
    assert acknowledged.next_phase is SubmitPhase.SUBMISSION_CONFIRMED
    advanced = advance_submit_prompt(op, acknowledged, snapshot, datetime.now(timezone.utc))
    assert advanced.acknowledged_turn == acknowledged_tail.last_user_turn

    complete_tail = replace(
        acknowledged_tail,
        last_assistant_turn=TurnRef("assistant-2", "assistant"),
        assistant_streaming=False,
        assistant_completed=True,
        transcript_revision=3,
    )
    completed_snapshot = replace(
        snapshot,
        revision=ObservationRevision(0, 3, 3),
        transcript_tail=Observed.present(
            complete_tail,
            evidence=(),
            observed_at=snapshot.captured_at,
            revision=ObservationRevision(0, 3, 3),
        ),
    )
    complete = reconcile_submit_prompt(
        replace(op, envelope=replace(op.envelope, phase=SubmitPhase.AWAITING_COMPLETION)),
        completed_snapshot,
        datetime.now(timezone.utc),
    )
    assert complete.kind is ControllerDecisionKind.SUCCEED


def test_prompt_reconciliation_requires_correlated_ack_and_fresh_empty_after_clear() -> None:
    """Changed text is not an ack, and clearing cannot fall through to insertion."""

    now = datetime.now(timezone.utc)
    unrelated = replace(
        _ready_snapshot(),
        revision=ObservationRevision(0, 2, 2),
        composer=Observed.present(
            ComposerState(
                "different text",
                "different text",
                "different-fingerprint",
                True,
                True,
                ComposerActionability.ACTIONABLE,
                False,
                True,
            ),
            evidence=(),
            observed_at=now,
            revision=ObservationRevision(0, 2, 2),
        ),
    )
    awaiting = _operation(SubmitPhase.AWAITING_ACKNOWLEDGMENT, commit="commit-1")
    changed = reconcile_submit_prompt(awaiting, unrelated, now)
    assert (changed.kind, changed.next_phase, changed.action) == (
        ControllerDecisionKind.OBSERVE_MORE,
        SubmitPhase.AWAITING_ACKNOWLEDGMENT,
        None,
    )

    empty_after_commit = replace(
        unrelated,
        composer=Observed.present(
            replace(
                unrelated.composer.value,
                text="",
                normalized_text="",
                content_fingerprint="empty",
            ),
            evidence=(),
            observed_at=now,
            revision=unrelated.revision,
        ),
    )
    acknowledged = reconcile_submit_prompt(awaiting, empty_after_commit, now)
    assert (acknowledged.kind, acknowledged.next_phase) == (
        ControllerDecisionKind.SUCCEED,
        SubmitPhase.SUCCEEDED,
    )

    clearing = _operation(SubmitPhase.CLEARING_COMPOSER)
    clear = reconcile_submit_prompt(clearing, unrelated, now)
    assert clear.kind is ControllerDecisionKind.EMIT_ACTION
    assert clear.next_phase is SubmitPhase.CLEARING_COMPOSER
    after_clear = advance_submit_prompt(clearing, clear, unrelated, now)

    unchanged = reconcile_submit_prompt(after_clear, unrelated, now)
    assert (unchanged.kind, unchanged.next_phase, unchanged.action) == (
        ControllerDecisionKind.OBSERVE_MORE,
        SubmitPhase.CLEARING_COMPOSER,
        None,
    )

    empty = replace(
        unrelated,
        revision=ObservationRevision(0, 3, 3),
        composer=Observed.present(
            ComposerState(
                "",
                "",
                "empty-fingerprint",
                True,
                True,
                ComposerActionability.ACTIONABLE,
                False,
                True,
            ),
            evidence=(),
            observed_at=now,
            revision=ObservationRevision(0, 3, 3),
        ),
    )
    verified_empty = reconcile_submit_prompt(after_clear, empty, now)
    assert (verified_empty.kind, verified_empty.next_phase, verified_empty.action) == (
        ControllerDecisionKind.OBSERVE_MORE,
        SubmitPhase.INSERTING_PAYLOAD,
        None,
    )
    ready_to_insert = advance_submit_prompt(after_clear, verified_empty, empty, now)
    insert = reconcile_submit_prompt(ready_to_insert, empty, now)
    assert (insert.kind, insert.next_phase) == (
        ControllerDecisionKind.EMIT_ACTION,
        SubmitPhase.VERIFYING_PAYLOAD,
    )
