"""Trace tests for the pure verified structured-question reconciler."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.questions import (
    AnswerQuestionOperation,
    AnswerQuestionPhase,
    QuestionAnswerRequest,
    advance_answer_question,
    question_fingerprint,
    reconcile_answer_question,
)
from murder.llm.harness_control.model import (
    AnswerQuestion,
    ChoiceState,
    ControllerDecisionKind,
    DuplicatePolicy,
    HarnessId,
    Knowledge,
    ObservationRevision,
    Observed,
    OperationEnvelope,
    OperationStatus,
    QuestionState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.actions import QuestionAnswerMode, QuestionChoiceSelection

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def _question(*, disabled: bool | None = False, summary: tuple[str, ...] = ()) -> QuestionState:
    return QuestionState(
        "question-1",
        "Choose deployments",
        (
            ChoiceState("blue", "Blue", checked=False, disabled=disabled, highlighted=True),
            ChoiceState("green", "Green", checked=False, disabled=disabled),
        ),
        "multi_select",
        None,
        (),
        True,
        "",
        "Submit",
        "Decline",
        summary,
    )


def _snapshot(question: QuestionState | None, revision: ObservationRevision) -> object:
    snapshot = unknown_snapshot(HarnessId("codex"), captured_at=NOW, revision=revision)
    observed = (
        Observed.present(question, evidence=(), observed_at=NOW, revision=revision)
        if question is not None
        else Observed.without_value(
            Knowledge.ABSENT, observed_at=NOW, revision=revision, explanation="dialog closed"
        )
    )
    return replace(snapshot, question=observed)


def _request(question: QuestionState, mode: QuestionAnswerMode = QuestionAnswerMode.MULTIPLE):
    targets = (
        QuestionChoiceSelection("blue", "Blue"),
        QuestionChoiceSelection("green", "Green"),
    )
    return QuestionAnswerRequest("question-1", question_fingerprint(question), mode, targets)


def _operation(
    phase: AnswerQuestionPhase,
    question: QuestionState,
    *,
    action_id: str | None = None,
    baseline: ObservationRevision | None = None,
) -> AnswerQuestionOperation:
    envelope = OperationEnvelope(
        "question-op",
        "answer_question",
        OperationStatus.RUNNING,
        phase,
        NOW,
        NOW,
        NOW + timedelta(minutes=1),
    )
    return AnswerQuestionOperation(envelope, _request(question), action_id, baseline)


def test_multiselect_action_preserves_semantic_choices_and_is_never_replayed() -> None:
    question = _question()
    snapshot = _snapshot(question, ObservationRevision(0, 2, 2))
    decision = reconcile_answer_question(
        _operation(AnswerQuestionPhase.READY_TO_ANSWER, question), snapshot, NOW
    )

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert isinstance(decision.action, AnswerQuestion)
    assert decision.action.duplicate_policy is DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY
    assert decision.action.selections == (
        QuestionChoiceSelection("blue", "Blue"),
        QuestionChoiceSelection("green", "Green"),
    )


def test_disabled_or_changed_choice_escalates_instead_of_navigating_by_row() -> None:
    requested = _question()
    visible = _question(disabled=True)
    decision = reconcile_answer_question(
        _operation(AnswerQuestionPhase.READY_TO_ANSWER, requested),
        _snapshot(visible, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_answered_summary_is_fresh_acknowledgment() -> None:
    question = _question(summary=("Selected: Blue, Green",))
    decision = reconcile_answer_question(
        _operation(
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            question,
            action_id="answer-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(question, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.SUCCEED


def test_ambiguous_post_answer_never_emits_a_second_answer() -> None:
    question = _question()
    decision = reconcile_answer_question(
        _operation(
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            question,
            action_id="answer-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(question, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_question_disappearance_is_ambiguous_without_an_answered_summary() -> None:
    question = _question()
    decision = reconcile_answer_question(
        _operation(
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            question,
            action_id="answer-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(None, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.OBSERVE_MORE


def test_post_answer_timeout_escalates_instead_of_failing_or_replaying() -> None:
    question = _question()
    decision = reconcile_answer_question(
        _operation(
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            question,
            action_id="answer-1",
            baseline=ObservationRevision(0, 1, 1),
        ),
        _snapshot(question, ObservationRevision(0, 2, 2)),
        NOW + timedelta(minutes=2),
    )

    assert decision.kind is ControllerDecisionKind.ESCALATE
    assert decision.action is None


def test_custom_answer_requires_explicit_custom_affordance() -> None:
    question = _question()
    request = QuestionAnswerRequest(
        "question-1",
        question_fingerprint(question),
        QuestionAnswerMode.CUSTOM,
        (),
        "explain why",
    )
    envelope = OperationEnvelope(
        "question-op",
        "answer_question",
        OperationStatus.RUNNING,
        AnswerQuestionPhase.READY_TO_ANSWER,
        NOW,
        NOW,
        NOW + timedelta(minutes=1),
    )
    decision = reconcile_answer_question(
        AnswerQuestionOperation(envelope, request),
        _snapshot(question, ObservationRevision(0, 2, 2)),
        NOW,
    )

    assert decision.kind is ControllerDecisionKind.EMIT_ACTION
    assert isinstance(decision.action, AnswerQuestion)
    assert decision.action.custom_answer == "explain why"


def test_answer_action_records_identity_and_freshness_before_emission() -> None:
    question = _question()
    snapshot = _snapshot(question, ObservationRevision(0, 2, 2))
    op = _operation(AnswerQuestionPhase.READY_TO_ANSWER, question)
    decision = reconcile_answer_question(op, snapshot, NOW)
    advanced = advance_answer_question(op, decision, snapshot, NOW)
    assert advanced.answer_action_id == decision.action.action_id
    assert advanced.baseline_revision == snapshot.revision
