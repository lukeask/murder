"""Controller-owned live loop for verified prompt submission."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from murder.llm.harness_control.capabilities.submit_prompt import (
    advance_submit_prompt,
    reconcile_submit_prompt,
)
from murder.llm.harness_control.model.actions import InputChunk
from murder.llm.harness_control.model.evidence import TerminalFrame
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationOutcome,
    OperationStatus,
    PromptPayload,
    SubmitPhase,
    SubmitPromptOperation,
    SubmitPromptRequest,
    SubmitPromptResult,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.controller import HarnessController


class FrameObserver(Protocol):
    """Read-only source of immutable terminal captures for one session."""

    async def capture_frame(self) -> TerminalFrame: ...


@dataclass(frozen=True, slots=True)
class PromptDriverPolicy:
    observation_interval: timedelta = timedelta(milliseconds=250)
    maximum_observations: int = 240


DEFAULT_PROMPT_DRIVER_POLICY = PromptDriverPolicy()


class VerifiedPromptDriver:
    """Runs one submit operation from observed reality to verified outcome.

    The driver owns polling and reconciliation.  Adapters only parse evidence
    and lower semantic actions; the actuator remains the sole terminal writer.
    """

    def __init__(
        self,
        controller: HarnessController,
        observer: FrameObserver,
        *,
        policy: PromptDriverPolicy = DEFAULT_PROMPT_DRIVER_POLICY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._controller = controller
        self._observer = observer
        self._policy = policy
        self._sleep = sleep
        self._now = now

    async def submit(
        self,
        chunks: tuple[InputChunk, ...],
        *,
        operation_id: str | None = None,
        await_completion: bool = False,
        submission_deadline: timedelta = timedelta(seconds=60),
        completion_deadline: timedelta | None = None,
    ) -> SubmitPromptResult:
        """Submit only after a fresh observed payload acknowledgment.

        It intentionally never sends a second Enter.  Restart recovery loads
        the persisted operation and reconciles it with a new capture instead of
        calling this method from phase zero.
        """

        op = self.create_operation(
            chunks,
            operation_id=operation_id,
            await_completion=await_completion,
            submission_deadline=submission_deadline,
            completion_deadline=completion_deadline,
        )
        await self._controller.persist_operation(op)
        return await self.resume(op)

    def create_operation(
        self,
        chunks: tuple[InputChunk, ...],
        *,
        operation_id: str | None = None,
        await_completion: bool = False,
        submission_deadline: timedelta = timedelta(seconds=60),
        completion_deadline: timedelta | None = None,
    ) -> SubmitPromptOperation:
        if not chunks:
            raise ValueError("a prompt submission requires at least one input chunk")
        normalized = "".join(chunk.text for chunk in chunks)
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        started = self._now()
        payload = PromptPayload(chunks, normalized, fingerprint)
        return SubmitPromptOperation(
            envelope=OperationEnvelope(
                operation_id=operation_id or str(uuid4()),
                capability="submit_prompt",
                status=OperationStatus.PENDING,
                phase=SubmitPhase.CREATED,
                created_at=started,
                updated_at=started,
                deadline=started + submission_deadline,
            ),
            request=SubmitPromptRequest(
                payload,
                await_completion,
                submission_deadline,
                completion_deadline,
            ),
            payload_fingerprint=fingerprint,
        )

    async def resume(self, op: SubmitPromptOperation) -> SubmitPromptResult:
        for observation_number in range(self._policy.maximum_observations):
            await self._controller.ingest_frame(await self._observer.capture_frame())
            result = await self._controller.reconcile_once(
                op,
                reconcile_submit_prompt,
                phase_name=op.envelope.phase.name,
                advance=advance_submit_prompt,
                priority=IntentPriority.PROMPT_SUBMISSION,
                decided_at=self._now(),
            )
            op = result.operation  # type: ignore[assignment]
            outcome = _terminal_outcome(result.decision.kind.name)
            if outcome is not None:
                if outcome is OperationOutcome.SUBMITTED and op.request.await_completion:
                    outcome = OperationOutcome.COMPLETED
                return SubmitPromptResult(
                    op.envelope.operation_id,
                    outcome,
                    op.acknowledged_turn,
                    op.completion_turn,
                    op.envelope.warnings,
                )
            if observation_number + 1 < self._policy.maximum_observations:
                await self._sleep(self._policy.observation_interval.total_seconds())
        result = await self._controller.reconcile_once(
            op,
            _observation_budget_exhausted,
            phase_name=op.envelope.phase.name,
            advance=advance_submit_prompt,
            priority=IntentPriority.PROMPT_SUBMISSION,
            decided_at=self._now(),
        )
        op = result.operation  # type: ignore[assignment]
        return SubmitPromptResult(
            op.envelope.operation_id,
            _terminal_outcome(result.decision.kind.name) or OperationOutcome.ESCALATED,
            op.acknowledged_turn,
            op.completion_turn,
            op.envelope.warnings,
        )


def _terminal_outcome(decision_kind: str) -> OperationOutcome | None:
    if decision_kind == "SUCCEED":
        return OperationOutcome.SUBMITTED
    if decision_kind == "FAIL":
        return OperationOutcome.FAILED
    if decision_kind == "ESCALATE":
        return OperationOutcome.ESCALATED
    return None


def _observation_budget_exhausted(
    _operation: SubmitPromptOperation,
    _snapshot: object,
    _now: datetime,
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE,
        SubmitPhase.ESCALATED,
        None,
        "prompt observation budget exhausted without verified outcome",
    )


__all__ = ["FrameObserver", "PromptDriverPolicy", "VerifiedPromptDriver"]
