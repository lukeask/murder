"""Deadline traces for verified question and permission decisions."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from murder.llm.harness_control.capabilities.permissions import (
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
)
from murder.llm.harness_control.capabilities.questions import (
    AnswerQuestionOperation,
    AnswerQuestionPhase,
    QuestionAnswerRequest,
)
from murder.llm.harness_control.model.actions import QuestionAnswerMode
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
    ReconciliationResult,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.session import (
    StructuredDecisionTimingPolicy,
    VerifiedHarnessControlSession,
)

START = datetime(2035, 7, 12, 12, tzinfo=timezone.utc)


class _FakeClock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class _Controller:
    def __init__(self) -> None:
        self.snapshot = object()
        self.decided_at: list[datetime] = []

    async def reconcile_once(self, operation, reconcile, *, advance, decided_at, **_kwargs):
        self.decided_at.append(decided_at)
        decision = reconcile(operation, self.snapshot, decided_at)
        advanced = advance(operation, decision, self.snapshot, decided_at)
        return ReconciliationResult(advanced, decision)


def _session(
    clock: _FakeClock, interval: timedelta
) -> tuple[VerifiedHarnessControlSession, _Controller, list[None]]:
    controller = _Controller()
    session = VerifiedHarnessControlSession(
        controller,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        harness_id=HarnessId("codex"),
        terminal_session="timing-test",
        connection=sqlite3.connect(":memory:"),
        persistence_session_id=None,
        structured_decision_timing=StructuredDecisionTimingPolicy(
            clock=clock.now,
            sleep=clock.sleep,
            observation_interval=interval,
        ),
    )
    observations: list[None] = []

    async def observe_once() -> None:
        observations.append(None)

    session.observe_once = observe_once  # type: ignore[method-assign]
    return session, controller, observations


def _operation(deadline: timedelta) -> AnswerQuestionOperation:
    return AnswerQuestionOperation(
        OperationEnvelope(
            "timed-question",
            "answer_question",
            OperationStatus.PENDING,
            AnswerQuestionPhase.CREATED,
            START,
            START,
            START + deadline,
        ),
        QuestionAnswerRequest("question-1", None, QuestionAnswerMode.CUSTOM, custom_answer="yes"),
    )


def _acknowledge_at(acknowledged_at: datetime):
    def reconcile(operation, _snapshot, now):
        if now >= operation.envelope.deadline:
            return ControllerDecision(
                ControllerDecisionKind.FAIL,
                AnswerQuestionPhase.FAILED,
                None,
                "question answer deadline exceeded before emission",
            )
        if now >= acknowledged_at:
            return ControllerDecision(
                ControllerDecisionKind.SUCCEED,
                AnswerQuestionPhase.ANSWERED,
                None,
                "question answer acknowledged",
            )
        return ControllerDecision(
            ControllerDecisionKind.OBSERVE_MORE,
            AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT,
            None,
            "awaiting acknowledgment",
        )

    return reconcile


def _advance(operation, _decision, _snapshot, _now):
    return operation


@pytest.mark.parametrize(
    ("deadline", "interval"),
    (
        (timedelta(milliseconds=100), timedelta(milliseconds=25)),
        (timedelta(minutes=2), timedelta(seconds=30)),
        (timedelta(minutes=5), timedelta(minutes=1)),
    ),
    ids=("short", "default", "longer"),
)
def test_structured_decision_observes_until_requested_deadline(
    deadline: timedelta, interval: timedelta
) -> None:
    clock = _FakeClock()
    session, controller, _ = _session(clock, interval)
    operation = _operation(deadline)

    result = asyncio.run(
        session._drive_structured(  # noqa: SLF001 -- focused timing trace
            operation,
            _acknowledge_at(START + deadline - interval),
            _advance,
            IntentPriority.PROMPT_SUBMISSION,
        )
    )

    assert result is True
    assert controller.decided_at[-1] == START + deadline - interval
    assert all(delay <= interval.total_seconds() for delay in clock.sleeps)


def test_structured_decision_clamps_sleep_and_reconciles_once_at_expiry() -> None:
    clock = _FakeClock()
    session, controller, observations = _session(clock, timedelta(seconds=1))
    operation = _operation(timedelta(milliseconds=75))

    result = asyncio.run(
        session._drive_structured(  # noqa: SLF001 -- focused timing trace
            operation,
            _acknowledge_at(START + timedelta(seconds=1)),
            _advance,
            IntentPriority.PROMPT_SUBMISSION,
        )
    )

    assert result is False
    assert clock.sleeps == [0.075]
    assert controller.decided_at == [START, START + timedelta(milliseconds=75)]
    assert len(observations) == len(controller.decided_at)


def test_acknowledgment_immediately_before_expiry_succeeds_but_after_expiry_does_not() -> None:
    deadline = timedelta(seconds=1)
    interval = timedelta(milliseconds=100)

    before_clock = _FakeClock()
    before_session, _, _ = _session(before_clock, interval)
    before = asyncio.run(
        before_session._drive_structured(  # noqa: SLF001 -- focused timing trace
            _operation(deadline),
            _acknowledge_at(START + deadline - interval),
            _advance,
            IntentPriority.PROMPT_SUBMISSION,
        )
    )

    after_clock = _FakeClock()
    after_session, _, _ = _session(after_clock, interval)
    after = asyncio.run(
        after_session._drive_structured(  # noqa: SLF001 -- focused timing trace
            _operation(deadline),
            _acknowledge_at(START + deadline + timedelta(microseconds=1)),
            _advance,
            IntentPriority.PROMPT_SUBMISSION,
        )
    )

    assert before is True
    assert after is False


@pytest.mark.parametrize("deadline", (timedelta(), -timedelta(microseconds=1)))
def test_structured_decision_rejects_non_positive_requested_deadlines(deadline: timedelta) -> None:
    clock = _FakeClock()
    session, _, _ = _session(clock, timedelta(milliseconds=1))
    request = QuestionAnswerRequest(
        "question-1", None, QuestionAnswerMode.CUSTOM, custom_answer="yes"
    )

    with pytest.raises(ValueError, match="question answer deadline must be positive"):
        asyncio.run(session.answer_question(request, deadline=deadline))

    permission = PermissionAnswerRequest(
        "permission-1",
        None,
        PermissionResponseTarget("allow-once", "Allow once", PermissionDecisionKind.ALLOW_ONCE),
    )
    with pytest.raises(ValueError, match="permission answer deadline must be positive"):
        asyncio.run(session.answer_permission(permission, deadline=deadline))
