"""Controller-owned live loop for verified usage collection.

Usage is a semantic operation, not an ad-hoc slash-command probe.  This
driver captures and persists a frame before every reconciliation and leaves
all harness syntax to ``RequestUsage`` lowering.  A successfully emitted
request is therefore only a request for later evidence; it is never reported
as collected usage by itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Protocol
from uuid import uuid4

from murder.llm.harness_control.capabilities.usage import (
    UsageOperation,
    UsagePhase,
    UsageRequest,
    advance_usage,
    reconcile_usage,
)
from murder.llm.harness_control.model.evidence import TerminalFrame
from murder.llm.harness_control.model.observations import Knowledge, UsageState
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.controller import HarnessController


class UsageFrameObserver(Protocol):
    """Read-only source of frames for one usage operation."""

    async def capture_frame(self) -> TerminalFrame: ...


class UsageCollectionOutcome(Enum):
    """Semantic result of observing or collecting usage."""

    COLLECTED = auto()
    ESCALATED = auto()


@dataclass(frozen=True, slots=True)
class UsageCollectionResult:
    operation_id: str
    outcome: UsageCollectionOutcome
    usage: UsageState | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UsageDriverPolicy:
    observation_interval: timedelta = timedelta(milliseconds=250)
    maximum_observations: int = 120

    def __post_init__(self) -> None:
        if self.maximum_observations <= 0:
            raise ValueError("usage driver requires at least one observation")


DEFAULT_USAGE_DRIVER_POLICY = UsageDriverPolicy()


class VerifiedUsageDriver:
    """Drive one ``UsageOperation`` using fresh evidence and one actuator.

    The operation can complete from already-visible current usage.  Otherwise
    it records and emits exactly the adapter-lowered request, then waits for a
    later observation revision carrying usable usage evidence.  It does not
    retry a slash command after an ambiguous or unsupported lowering.
    """

    def __init__(
        self,
        controller: HarnessController,
        observer: UsageFrameObserver,
        *,
        policy: UsageDriverPolicy = DEFAULT_USAGE_DRIVER_POLICY,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._controller = controller
        self._observer = observer
        self._policy = policy
        self._sleep = sleep
        self._now = now
        self._last_collected_usage: UsageState | None = None

    async def collect(
        self,
        request: UsageRequest,
        *,
        operation_id: str | None = None,
    ) -> UsageCollectionResult:
        """Collect usage according to a typed freshness/source request."""

        operation = self.create_operation(request, operation_id=operation_id)
        await self._controller.persist_operation(operation)
        return await self.resume(operation)

    def create_operation(
        self, request: UsageRequest, *, operation_id: str | None = None
    ) -> UsageOperation:
        if request.deadline <= timedelta():
            raise ValueError("usage deadline must be positive")
        started = self._now()
        return UsageOperation(
            envelope=OperationEnvelope(
                operation_id=operation_id or str(uuid4()),
                capability="usage",
                status=OperationStatus.PENDING,
                phase=UsagePhase.CREATED,
                created_at=started,
                updated_at=started,
                deadline=started + request.deadline,
            ),
            request=request,
        )

    async def collect_usage(
        self,
        *,
        deadline: timedelta = timedelta(minutes=1),
        require_current: bool = False,
        preferred_source: str | None = None,
        operation_id: str | None = None,
    ) -> UsageCollectionResult:
        """Convenience entry point for the public usage capability."""

        return await self.collect(
            UsageRequest(deadline, require_current, preferred_source),
            operation_id=operation_id,
        )

    async def resume(self, operation: UsageOperation) -> UsageCollectionResult:
        """Recover persisted state through fresh observations only.

        In particular, ``REQUEST_EMITTED`` resumes at observation, so it can
        verify a previous request but cannot create another physical request.
        """

        if operation.envelope.capability != "usage":
            raise ValueError("usage driver only accepts usage operations")
        if operation.envelope.phase is UsagePhase.SUCCEEDED:
            return self._result(operation, UsageCollectionOutcome.COLLECTED)
        if operation.envelope.phase is UsagePhase.ESCALATED:
            return self._result(operation, UsageCollectionOutcome.ESCALATED)

        current = operation
        for observation_number in range(self._policy.maximum_observations):
            await self._controller.ingest_frame(await self._observer.capture_frame())
            observed_usage = self._controller.snapshot.usage
            if observed_usage.knowledge is Knowledge.PRESENT and observed_usage.value is not None:
                self._last_collected_usage = observed_usage.value
            result = await self._controller.reconcile_once(
                current,
                reconcile_usage,
                phase_name=current.envelope.phase.name,
                advance=advance_usage,
                priority=IntentPriority.BACKGROUND_USAGE,
                decided_at=self._now(),
            )
            current = result.operation  # type: ignore[assignment]
            if result.decision.kind is ControllerDecisionKind.SUCCEED:
                return self._result(current, UsageCollectionOutcome.COLLECTED)
            if result.decision.kind in {
                ControllerDecisionKind.FAIL,
                ControllerDecisionKind.ESCALATE,
            }:
                return self._result(current, UsageCollectionOutcome.ESCALATED)
            if observation_number + 1 < self._policy.maximum_observations:
                await self._sleep(self._policy.observation_interval.total_seconds())

        result = await self._controller.reconcile_once(
            current,
            _observation_budget_exhausted,
            phase_name=current.envelope.phase.name,
            advance=advance_usage,
            priority=IntentPriority.BACKGROUND_USAGE,
            decided_at=self._now(),
        )
        return self._result(result.operation, UsageCollectionOutcome.ESCALATED)  # type: ignore[arg-type]

    def _result(
        self,
        operation: UsageOperation,
        outcome: UsageCollectionOutcome,
    ) -> UsageCollectionResult:
        observed = self._controller.snapshot.usage
        usage = (
            observed.value
            if observed.knowledge is Knowledge.PRESENT and observed.value is not None
            else self._last_collected_usage
        )
        return UsageCollectionResult(
            operation.envelope.operation_id,
            outcome,
            usage,
            tuple(warning.message for warning in operation.envelope.warnings),
        )


def _observation_budget_exhausted(
    _operation: UsageOperation,
    _snapshot: object,
    _now: datetime,
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE,
        UsagePhase.ESCALATED,
        None,
        "usage observation budget exhausted without fresh usable evidence",
    )


__all__ = [
    "DEFAULT_USAGE_DRIVER_POLICY",
    "UsageCollectionOutcome",
    "UsageCollectionResult",
    "UsageDriverPolicy",
    "UsageFrameObserver",
    "VerifiedUsageDriver",
]
