from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.capabilities.submit_prompt import (
    advance_submit_prompt,
    reconcile_submit_prompt,
)
from murder.llm.harness_control.model import (
    ComposerActionability,
    ComposerState,
    ControllerDecision,
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
from murder.llm.harness_control.model.evidence import FrameId, TerminalFrame

ROOT = Path(__file__).parents[2]
HARD_WRAPPED_PROMPT = (
    "review the existing tinyboard project as the final maintainer. inspect domain behavior, "
    "json storage durability, cli error handling, packaging, documentation, and tests. run the "
    "full suite and a realistic cli smoke test. correct any concrete bug or inconsistency you "
    "find with small focused changes and add regression tests; otherwise leave the implementation "
    "alone. finish with a concise account of what you verified."
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


def test_fresh_collapsed_paste_requires_payload_derived_visible_tail() -> None:
    text = "same-length body " * 20 + "the authentic payload has this unique visible ending"
    tail = text[-64:]
    prefix_count = len(text) - len(tail) + 1
    operation = _operation(SubmitPhase.VERIFYING_PAYLOAD)
    operation = replace(
        operation,
        request=replace(
            operation.request,
            payload=PromptPayload(
                (
                    InputChunk(
                        text,
                        InputProvenance.USER_PASTE_BLOCK,
                        f"chunk:{operation.payload_fingerprint}",
                    ),
                ),
                text,
                operation.payload_fingerprint,
            ),
        ),
        insertion_revision=ObservationRevision(0, 1, 1),
    )
    snapshot = replace(
        _ready_snapshot(),
        revision=ObservationRevision(0, 2, 2),
    )

    def with_summary(summary_tail: str):
        summary = f"[Pasted Content {prefix_count} chars] {summary_tail}"
        composer = replace(
            snapshot.composer.value,
            text=summary,
            normalized_text=summary,
            content_fingerprint="collapsed-summary",
        )
        return replace(
            snapshot,
            composer=Observed.present(
                composer,
                evidence=(),
                observed_at=snapshot.captured_at,
                revision=snapshot.revision,
            ),
        )

    unrelated = reconcile_submit_prompt(
        operation,
        with_summary("x" * len(tail)),
        datetime.now(timezone.utc),
    )
    stale = reconcile_submit_prompt(
        operation,
        replace(with_summary(tail), revision=operation.insertion_revision),
        datetime.now(timezone.utc),
    )
    verified = reconcile_submit_prompt(
        operation,
        with_summary(tail),
        datetime.now(timezone.utc),
    )

    assert unrelated.kind is ControllerDecisionKind.OBSERVE_MORE
    assert unrelated.next_phase is SubmitPhase.VERIFYING_PAYLOAD
    assert unrelated.action is None
    assert stale.next_phase is SubmitPhase.VERIFYING_PAYLOAD
    assert stale.action is None
    assert verified.kind is ControllerDecisionKind.OBSERVE_MORE
    assert verified.next_phase is SubmitPhase.READY_TO_COMMIT


def test_recorded_codex_hard_wrap_verifies_the_exact_intended_payload() -> None:
    recorded_frame = (
        ROOT
        / "tests"
        / "fixtures"
        / "harness_panes"
        / "codex_01445_hard_wrapped_paste_tail.ansi"
    ).read_text() + "\n"
    assert hashlib.sha256(recorded_frame.encode()).hexdigest() == (
        "720a6931fc8f14cfc923b7429b3a5bc8eed04a7f43acc17535ad4c5a6e66d75d"
    )
    frame = TerminalFrame(
        FrameId("819a0b4b-2b81-4e5b-9863-9263330d36f6"),
        HarnessId("codex"),
        datetime(2026, 7, 18, 7, 2, tzinfo=timezone.utc),
        220,
        50,
        recorded_frame,
        True,
        0,
        45,
    )
    adapter = CodexHarnessAdapter()
    evidence = adapter.parse_evidence(frame, ())
    prior = unknown_snapshot(
        HarnessId("codex"),
        captured_at=frame.captured_at,
        revision=ObservationRevision(0, 38, 4),
    )
    updates = adapter.project_observations(evidence, prior).updates
    snapshot = replace(
        prior,
        revision=updates["composer"].revision,
        **updates,
    )  # type: ignore[arg-type]
    fingerprint = hashlib.sha256(HARD_WRAPPED_PROMPT.encode()).hexdigest()
    operation = _operation(SubmitPhase.VERIFYING_PAYLOAD)
    operation = replace(
        operation,
        request=replace(
            operation.request,
            payload=PromptPayload(
                (
                    InputChunk(
                        HARD_WRAPPED_PROMPT,
                        InputProvenance.USER_PASTE_BLOCK,
                        f"chunk:{fingerprint}",
                    ),
                ),
                HARD_WRAPPED_PROMPT,
                fingerprint,
            ),
        ),
        payload_fingerprint=fingerprint,
        insertion_revision=prior.revision,
    )

    assert snapshot.composer.value is not None
    assert snapshot.composer.value.text is not None
    assert snapshot.composer.value.normalized_text is not None
    assert "implement\nation" in snapshot.composer.value.text
    assert "implement ation" in snapshot.composer.value.normalized_text

    decision = reconcile_submit_prompt(operation, snapshot, frame.captured_at)

    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE
    assert decision.next_phase is SubmitPhase.READY_TO_COMMIT
    assert decision.predicates[1].value.name == "TRUE"


def test_visual_row_matching_preserves_real_whitespace_and_rejects_near_matches() -> None:
    snapshot = replace(_ready_snapshot(), revision=ObservationRevision(0, 2, 2))

    def decision_for(expected: str) -> ControllerDecision:
        fingerprint = hashlib.sha256(expected.encode()).hexdigest()
        composer = replace(
            snapshot.composer.value,
            text="implement\nation",
            normalized_text="implement ation",
            content_fingerprint=hashlib.sha256(b"implement ation").hexdigest(),
        )
        observed = replace(
            snapshot,
            composer=Observed.present(
                composer,
                evidence=(),
                observed_at=snapshot.captured_at,
                revision=snapshot.revision,
            ),
        )
        operation = _operation(SubmitPhase.VERIFYING_PAYLOAD)
        operation = replace(
            operation,
            request=replace(
                operation.request,
                payload=PromptPayload(
                    (InputChunk(expected, InputProvenance.USER_TYPED, "chunk"),),
                    expected,
                    fingerprint,
                ),
            ),
            payload_fingerprint=fingerprint,
            insertion_revision=ObservationRevision(0, 1, 1),
        )
        return reconcile_submit_prompt(operation, observed, datetime.now(timezone.utc))

    real_space = decision_for("implement ation")
    hard_wrap = decision_for("implementation")
    adversarial = decision_for("implement station")

    assert real_space.next_phase is SubmitPhase.READY_TO_COMMIT
    assert hard_wrap.next_phase is SubmitPhase.READY_TO_COMMIT
    assert adversarial.next_phase is SubmitPhase.VERIFYING_PAYLOAD


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
