"""Live driver for exhaustive, verified interactive model discovery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import uuid4

from murder.llm.harness_control.capabilities.model_discovery import (
    DiscoverModelsOperation,
    DiscoverModelsRequest,
    DiscoverModelsResult,
    ModelDiscoveryPhase,
    advance_model_discovery,
    reconcile_model_discovery,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
)
from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.controller import HarnessController
from murder.llm.harness_control.runtime.model_driver import ModelFrameObserver

_MAXIMUM_OBSERVATIONS = 256


class VerifiedModelDiscoveryDriver:
    def __init__(
        self,
        controller: HarnessController,
        observer: ModelFrameObserver,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._controller = controller
        self._observer = observer
        self._sleep = sleep
        self._now = now

    async def discover(
        self, *, deadline: timedelta = timedelta(minutes=2)
    ) -> DiscoverModelsResult:
        if deadline <= timedelta():
            raise ValueError("model-discovery deadline must be positive")
        started = self._now()
        operation = DiscoverModelsOperation(
            OperationEnvelope(
                str(uuid4()),
                "discover_models",
                OperationStatus.PENDING,
                ModelDiscoveryPhase.CREATED,
                started,
                started,
                started + deadline,
            ),
            DiscoverModelsRequest(deadline),
        )
        await self._controller.persist_operation(operation)
        current = operation
        for index in range(_MAXIMUM_OBSERVATIONS):
            await self._controller.ingest_frame(await self._observer.capture_frame())
            result = await self._controller.reconcile_once(
                current,
                reconcile_model_discovery,
                phase_name=current.envelope.phase.name,
                advance=advance_model_discovery,
                priority=IntentPriority.MODEL_SELECTION,
                decided_at=self._now(),
            )
            current = cast(DiscoverModelsOperation, result.operation)
            if result.decision.kind in {
                ControllerDecisionKind.SUCCEED,
                ControllerDecisionKind.FAIL,
                ControllerDecisionKind.ESCALATE,
            }:
                break
            if index + 1 < _MAXIMUM_OBSERVATIONS:
                await self._sleep(0.25)
        succeeded = current.envelope.status is OperationStatus.SUCCEEDED
        return DiscoverModelsResult(
            current.envelope.operation_id,
            current.models,
            succeeded,
            tuple(warning.message for warning in current.envelope.warnings),
        )


__all__ = ["VerifiedModelDiscoveryDriver"]
