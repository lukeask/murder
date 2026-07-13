"""Controller-owned live loop for verified model selection.

The driver deliberately knows neither harness picker syntax nor model parsing.
It repeatedly supplies fresh frames to :class:`HarnessController`, then lets
the typed model-selection reconciler select semantic actions.  In particular,
it never interprets an emitted confirmation as activation and never replays an
unsafe confirmation after restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from murder.llm.harness_control.capabilities.model_selection import (
    ModelSelectionOutcome,
    ModelSelectionPhase,
    ModelTarget,
    SelectModelOperation,
    SelectModelRequest,
    SelectModelResult,
    advance_model_selection,
    reconcile_model_selection,
)
from murder.llm.harness_control.model.evidence import TerminalFrame
from murder.llm.harness_control.model.observations import Knowledge, ModelState
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.controller import HarnessController


class ModelFrameObserver(Protocol):
    """Read-only frame source for one model-selection operation."""

    async def capture_frame(self) -> TerminalFrame: ...


@dataclass(frozen=True, slots=True)
class ModelDriverPolicy:
    observation_interval: timedelta = timedelta(milliseconds=250)
    maximum_observations: int = 240

    def __post_init__(self) -> None:
        if self.maximum_observations <= 0:
            raise ValueError("model driver requires at least one observation")


DEFAULT_MODEL_DRIVER_POLICY = ModelDriverPolicy()


class VerifiedModelSelectionDriver:
    """Drive one typed model operation from current evidence to a result.

    ``resume`` accepts already-persisted semantic state.  It always captures a
    new frame before reconciliation, so an unsafe action record can only lead
    to fresh verification, recovery, or escalation -- never physical replay.
    """

    def __init__(
        self,
        controller: HarnessController,
        observer: ModelFrameObserver,
        *,
        policy: ModelDriverPolicy = DEFAULT_MODEL_DRIVER_POLICY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._controller = controller
        self._observer = observer
        self._policy = policy
        self._sleep = sleep
        self._now = now

    async def select(
        self,
        target: ModelTarget,
        *,
        deadline: timedelta = timedelta(minutes=3),
        operation_id: str | None = None,
    ) -> SelectModelResult:
        """Request configuration and independent active-model verification."""

        operation = self.create_operation(
            target, deadline=deadline, operation_id=operation_id
        )
        await self._controller.persist_operation(operation)
        return await self.resume(operation)

    def create_operation(
        self,
        target: ModelTarget,
        *,
        deadline: timedelta = timedelta(minutes=3),
        operation_id: str | None = None,
    ) -> SelectModelOperation:
        if deadline <= timedelta():
            raise ValueError("model-selection deadline must be positive")
        started = self._now()
        return SelectModelOperation(
            envelope=OperationEnvelope(
                operation_id=operation_id or str(uuid4()),
                capability="select_model",
                status=OperationStatus.PENDING,
                phase=ModelSelectionPhase.CREATED,
                created_at=started,
                updated_at=started,
                deadline=started + deadline,
            ),
            request=SelectModelRequest(target, deadline),
        )

    async def resume(self, operation: SelectModelOperation) -> SelectModelResult:
        """Recover a persisted operation from a fresh observation stream."""

        if operation.envelope.capability != "select_model":
            raise ValueError("model driver only accepts select_model operations")
        if (
            operation.envelope.status is not OperationStatus.RUNNING
            and operation.envelope.status is not OperationStatus.PENDING
        ):
            return self._result(operation)

        current = operation
        for observation_number in range(self._policy.maximum_observations):
            await self._controller.ingest_frame(await self._observer.capture_frame())
            result = await self._controller.reconcile_once(
                current,
                reconcile_model_selection,
                phase_name=current.envelope.phase.name,
                advance=advance_model_selection,
                priority=IntentPriority.MODEL_SELECTION,
                decided_at=self._now(),
            )
            current = result.operation  # type: ignore[assignment]
            if result.decision.kind in {
                ControllerDecisionKind.SUCCEED,
                ControllerDecisionKind.FAIL,
                ControllerDecisionKind.ESCALATE,
            }:
                return self._result(current)
            if observation_number + 1 < self._policy.maximum_observations:
                await self._sleep(self._policy.observation_interval.total_seconds())

        # Observation budget expiry is a recorded escalation, not a fabricated
        # result and not an invitation to replay a model confirmation.
        result = await self._controller.reconcile_once(
            current,
            _observation_budget_exhausted,
            phase_name=current.envelope.phase.name,
            advance=advance_model_selection,
            priority=IntentPriority.MODEL_SELECTION,
            decided_at=self._now(),
        )
        return self._result(result.operation)  # type: ignore[arg-type]

    def _result(self, operation: SelectModelOperation) -> SelectModelResult:
        active = self._active_model()
        outcome = {
            OperationStatus.SUCCEEDED: ModelSelectionOutcome.ACTIVATED,
            OperationStatus.FAILED: ModelSelectionOutcome.FAILED,
            OperationStatus.ESCALATED: ModelSelectionOutcome.ESCALATED,
        }.get(operation.envelope.status, ModelSelectionOutcome.ESCALATED)
        return SelectModelResult(
            operation.envelope.operation_id,
            outcome,
            active,
            tuple(warning.message for warning in operation.envelope.warnings),
        )

    def _active_model(self) -> ModelState | None:
        observed = self._controller.snapshot.active_model
        return observed.value if observed.knowledge is Knowledge.PRESENT else None


def _observation_budget_exhausted(
    operation: SelectModelOperation,
    _snapshot: object,
    _now: datetime,
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE,
        ModelSelectionPhase.AMBIGUOUS,
        None,
        "model-selection observation budget exhausted without verified active-model readback",
    )


__all__ = [
    "DEFAULT_MODEL_DRIVER_POLICY",
    "ModelFrameObserver",
    "ModelDriverPolicy",
    "VerifiedModelSelectionDriver",
]
