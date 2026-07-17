"""Pure, verified reconciliation for structured harness questions and menus.

Question parsing and key navigation belong to harness adapters.  This module
only binds an externally chosen semantic answer to the question that was
observed, then waits for fresh evidence that the answer was accepted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, auto
from uuid import NAMESPACE_URL, uuid5

from murder.llm.harness_control.model.actions import (
    AnswerQuestion,
    DuplicatePolicy,
    QuestionAnswerMode,
    QuestionChoiceSelection,
)
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    Knowledge,
    ObservationRevision,
    ObservationSnapshot,
    QuestionState,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.model.predicates import PredicateResult, TruthValue


class AnswerQuestionPhase(Enum):
    CREATED = auto()
    AWAITING_QUESTION = auto()
    READY_TO_ANSWER = auto()
    ANSWER_EMITTED = auto()
    AWAITING_ACKNOWLEDGMENT = auto()
    ANSWERED = auto()
    AMBIGUOUS = auto()
    FAILED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class QuestionAnswerRequest:
    """An external/user decision bound to an observed question identity."""

    question_id_hint: str | None
    question_fingerprint: str | None
    mode: QuestionAnswerMode
    selections: tuple[QuestionChoiceSelection, ...] = ()
    custom_answer: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.question_id_hint and not self.question_fingerprint:
            raise ValueError("a question answer requires an id hint or fingerprint")
        if self.mode in {QuestionAnswerMode.SINGLE, QuestionAnswerMode.MULTIPLE}:
            if not self.selections or self.custom_answer is not None:
                raise ValueError("choice answers require choices and no custom answer")
            if self.mode is QuestionAnswerMode.SINGLE and len(self.selections) != 1:
                raise ValueError("single-select answers require exactly one choice")
        elif self.mode is QuestionAnswerMode.CUSTOM:
            if self.selections or self.note is not None or not (self.custom_answer or "").strip():
                raise ValueError("custom answers require non-empty custom text only")
        elif self.mode is QuestionAnswerMode.DECLINE:
            if self.selections or self.custom_answer is not None or self.note is not None:
                raise ValueError("decline answers cannot include choices or custom text")
        if self.note is not None and not self.note.strip():
            raise ValueError("question notes must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class AnswerQuestionOperation:
    envelope: OperationEnvelope[AnswerQuestionPhase]
    request: QuestionAnswerRequest
    answer_action_id: str | None = None
    baseline_revision: ObservationRevision | None = None
    ambiguity_reason: str | None = None


def question_fingerprint(question: QuestionState) -> str:
    """Stable identity fallback for adapters that cannot expose a native id."""

    choices = "\x1e".join(
        f"{choice.stable_choice_id or ''}\x1f{choice.label.strip()}" for choice in question.choices
    )
    raw = f"{(question.prompt_text or '').strip()}\x1d{choices}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _predicate(
    snapshot: ObservationSnapshot,
    name: str,
    value: TruthValue,
    reason: str,
    *,
    evidence: tuple = (),
) -> PredicateResult:
    return PredicateResult(value, name, evidence, snapshot.revision, reason)


def question_matches(
    request: QuestionAnswerRequest, snapshot: ObservationSnapshot
) -> PredicateResult:
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "question_matches",
            TruthValue.UNKNOWN,
            f"question is {observed.knowledge.name.lower()}",
            evidence=observed.evidence,
        )
    question = observed.value
    if request.note is not None and "notes" not in question.visible_tabs:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.FALSE,
            "question does not expose a notes affordance",
            evidence=observed.evidence,
        )
    checks: list[bool] = []
    if request.question_id_hint:
        if question.question_id_hint is None:
            return _predicate(
                snapshot,
                "question_matches",
                TruthValue.UNKNOWN,
                "observed question has no stable id",
                evidence=observed.evidence,
            )
        checks.append(question.question_id_hint == request.question_id_hint)
    if request.question_fingerprint:
        checks.append(question_fingerprint(question) == request.question_fingerprint)
    return _predicate(
        snapshot,
        "question_matches",
        TruthValue.TRUE if all(checks) else TruthValue.FALSE,
        "question identity compared with the recorded decision",
        evidence=observed.evidence,
    )


def _find_choice(
    choices: tuple[ChoiceState, ...], target: QuestionChoiceSelection
) -> ChoiceState | None | bool:
    """Return choice, ``None`` if absent, or False when label lookup is ambiguous."""

    if target.stable_choice_id:
        return next(
            (choice for choice in choices if choice.stable_choice_id == target.stable_choice_id),
            None,
        )
    matches = [choice for choice in choices if choice.label == target.label]
    return matches[0] if len(matches) == 1 else (None if not matches else False)


def answer_is_available(  # noqa: PLR0911 -- each knowledge/choice outcome is explicit
    request: QuestionAnswerRequest, snapshot: ObservationSnapshot
) -> PredicateResult:
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.UNKNOWN,
            "question choices are not currently known",
            evidence=observed.evidence,
        )
    question = observed.value
    if request.mode is QuestionAnswerMode.CUSTOM:
        if question.allow_custom_answer is None:
            return _predicate(
                snapshot,
                "question_answer_available",
                TruthValue.UNKNOWN,
                "custom-answer affordance is unknown",
                evidence=observed.evidence,
            )
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.TRUE if question.allow_custom_answer else TruthValue.FALSE,
            "custom-answer affordance evaluated",
            evidence=observed.evidence,
        )
    if request.mode is QuestionAnswerMode.DECLINE:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.TRUE if question.decline_label else TruthValue.FALSE,
            "decline control evaluated",
            evidence=observed.evidence,
        )

    mode = (question.selection_mode or "").strip().lower().replace("-", "_")
    if not mode:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.UNKNOWN,
            "selection mode is unknown",
            evidence=observed.evidence,
        )
    is_multi = mode in {"multi", "multiple", "multi_select", "multiselect"}
    if request.mode is QuestionAnswerMode.SINGLE and is_multi:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.FALSE,
            "recorded single-select answer does not match a multi-select surface",
            evidence=observed.evidence,
        )
    if request.mode is QuestionAnswerMode.MULTIPLE and not is_multi:
        return _predicate(
            snapshot,
            "question_answer_available",
            TruthValue.FALSE,
            "recorded multi-select answer does not match this surface",
            evidence=observed.evidence,
        )
    for target in request.selections:
        match = _find_choice(question.choices, target)
        if match is False:
            return _predicate(
                snapshot,
                "question_answer_available",
                TruthValue.UNKNOWN,
                f"choice label {target.label!r} is ambiguous",
                evidence=observed.evidence,
            )
        if match is None:
            return _predicate(
                snapshot,
                "question_answer_available",
                TruthValue.FALSE,
                f"requested choice {target.label!r} is unavailable",
                evidence=observed.evidence,
            )
        if match.disabled is None:
            return _predicate(
                snapshot,
                "question_answer_available",
                TruthValue.UNKNOWN,
                f"disabled state is unknown for {target.label!r}",
                evidence=observed.evidence,
            )
        if match.disabled:
            return _predicate(
                snapshot,
                "question_answer_available",
                TruthValue.FALSE,
                f"requested choice {target.label!r} is disabled",
                evidence=observed.evidence,
            )
    return _predicate(
        snapshot,
        "question_answer_available",
        TruthValue.TRUE,
        "all requested choices are available",
        evidence=observed.evidence,
    )


def question_answer_acknowledged(  # noqa: PLR0911 - explicit acknowledgment states
    op: AnswerQuestionOperation, snapshot: ObservationSnapshot
) -> PredicateResult:
    if (
        op.answer_action_id is None
        or op.baseline_revision is None
        or snapshot.revision <= op.baseline_revision
    ):
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.UNKNOWN,
            "no fresh observation after answer emission",
        )
    observed = snapshot.question
    if observed.knowledge is Knowledge.ABSENT:
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.UNKNOWN,
            "question surface disappeared without a correlated answered summary",
            evidence=observed.evidence,
        )
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.UNKNOWN,
            f"question is {observed.knowledge.name.lower()}",
            evidence=observed.evidence,
        )
    matched = question_matches(op.request, snapshot)
    if matched.value is not TruthValue.TRUE:
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.UNKNOWN,
            "current question cannot be safely correlated",
            evidence=observed.evidence,
        )
    summary = "\n".join(observed.value.answered_summary).casefold()
    if op.request.mode is QuestionAnswerMode.DECLINE and any(
        marker in summary for marker in ("declined", "canceled", "interrupted")
    ):
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.TRUE,
            "question decline is explicitly acknowledged",
            evidence=observed.evidence,
        )
    expected = [target.label.casefold() for target in op.request.selections]
    if op.request.custom_answer:
        expected.append(op.request.custom_answer.casefold())
    if op.request.note:
        expected.append(op.request.note.casefold())
    if expected and summary and all(value in summary for value in expected):
        return _predicate(
            snapshot,
            "question_answer_acknowledged",
            TruthValue.TRUE,
            "answered summary contains the requested answer",
            evidence=observed.evidence,
        )
    return _predicate(
        snapshot,
        "question_answer_acknowledged",
        TruthValue.FALSE,
        "same question remains without a matching answered summary",
        evidence=observed.evidence,
    )


def _answer_action(op: AnswerQuestionOperation) -> AnswerQuestion:
    request = op.request
    action_id = str(uuid5(NAMESPACE_URL, f"{op.envelope.operation_id}:answer-question"))
    return AnswerQuestion(
        action_id,
        op.envelope.operation_id,
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        request.question_id_hint,
        request.mode,
        request.selections,
        request.custom_answer,
        request.note,
    )


def reconcile_answer_question(  # noqa: PLR0911, PLR0912 -- typed operation phases
    op: AnswerQuestionOperation, snapshot: ObservationSnapshot, now: datetime
) -> ControllerDecision:
    if op.envelope.deadline is not None and now >= op.envelope.deadline:
        if op.envelope.phase in {
            AnswerQuestionPhase.ANSWER_EMITTED,
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
        }:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerQuestionPhase.AMBIGUOUS,
                None,
                "question answer was emitted without verified acknowledgment before deadline",
            )
        return ControllerDecision(
            ControllerDecisionKind.FAIL,
            AnswerQuestionPhase.FAILED,
            None,
            "question answer deadline exceeded before emission",
        )
    if snapshot.health.requires_escalation:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            AnswerQuestionPhase.ESCALATED,
            None,
            "observation health requires escalation",
        )
    phase = op.envelope.phase
    if phase is AnswerQuestionPhase.CREATED:
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            AnswerQuestionPhase.AWAITING_QUESTION,
            None,
            "begin verified question answer",
        )
    if phase is AnswerQuestionPhase.AWAITING_QUESTION:
        identity = question_matches(op.request, snapshot)
        if identity.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                AnswerQuestionPhase.READY_TO_ANSWER,
                None,
                "target question is visible",
                (identity,),
            )
        if identity.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerQuestionPhase.ESCALATED,
                None,
                "a different question is visible",
                (identity,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            phase,
            None,
            "await target question evidence",
            (identity,),
        )
    if phase is AnswerQuestionPhase.READY_TO_ANSWER:
        identity = question_matches(op.request, snapshot)
        availability = answer_is_available(op.request, snapshot)
        predicates = (identity, availability)
        if identity.value is TruthValue.FALSE or availability.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerQuestionPhase.ESCALATED,
                None,
                "target question or requested answer changed",
                predicates,
            )
        if identity.value is not TruthValue.TRUE or availability.value is not TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.OBSERVE_MORE,
                phase,
                None,
                "answer preconditions remain uncertain",
                predicates,
            )
        if op.answer_action_id is not None:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerQuestionPhase.AMBIGUOUS,
                None,
                "an answer action already exists",
                predicates,
            )
        return ControllerDecision(
            ControllerDecisionKind.EMIT_ACTION,
            AnswerQuestionPhase.ANSWER_EMITTED,
            _answer_action(op),
            "answer verified question",
            predicates,
        )
    if phase in {AnswerQuestionPhase.ANSWER_EMITTED, AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT}:
        acknowledged = question_answer_acknowledged(op, snapshot)
        if acknowledged.value is TruthValue.TRUE:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                AnswerQuestionPhase.ANSWERED,
                None,
                "question answer acknowledged",
                (acknowledged,),
            )
        if acknowledged.value is TruthValue.FALSE:
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                AnswerQuestionPhase.AMBIGUOUS,
                None,
                "answer emission lacks compatible acknowledgment",
                (acknowledged,),
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            None,
            "await fresh question-answer acknowledgment",
            (acknowledged,),
        )
    if phase is AnswerQuestionPhase.AMBIGUOUS:
        return ControllerDecision(
            ControllerDecisionKind.ESCALATE,
            AnswerQuestionPhase.ESCALATED,
            None,
            op.ambiguity_reason or "ambiguous question answer",
        )
    return ControllerDecision(
        ControllerDecisionKind.FAIL,
        AnswerQuestionPhase.FAILED,
        None,
        f"invalid question-answer phase {phase.name}",
    )


def advance_answer_question(
    op: AnswerQuestionOperation,
    decision: ControllerDecision,
    snapshot: ObservationSnapshot,
    now: datetime,
) -> AnswerQuestionOperation:
    """Persist the unsafe answer intent and freshness boundary before effects."""

    status = _status_after(op.envelope.status, decision.kind)
    phase = (
        decision.next_phase
        if isinstance(decision.next_phase, AnswerQuestionPhase)
        else op.envelope.phase
    )
    action_history = op.envelope.action_history
    action_id = op.answer_action_id
    baseline = op.baseline_revision
    if decision.action is not None:
        action_history = (*action_history, decision.action.action_id)
        action_id = decision.action.action_id
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
        answer_action_id=action_id,
        baseline_revision=baseline,
        ambiguity_reason=decision.reason
        if decision.kind is ControllerDecisionKind.ESCALATE
        else op.ambiguity_reason,
    )


def _status_after(status: OperationStatus, kind: ControllerDecisionKind) -> OperationStatus:
    if kind is ControllerDecisionKind.SUCCEED:
        return OperationStatus.SUCCEEDED
    if kind is ControllerDecisionKind.FAIL:
        return OperationStatus.FAILED
    if kind is ControllerDecisionKind.ESCALATE:
        return OperationStatus.ESCALATED
    return OperationStatus.RUNNING if status is OperationStatus.PENDING else status


__all__ = [
    "AnswerQuestionOperation",
    "AnswerQuestionPhase",
    "QuestionAnswerRequest",
    "answer_is_available",
    "advance_answer_question",
    "question_answer_acknowledged",
    "question_fingerprint",
    "question_matches",
    "reconcile_answer_question",
]
