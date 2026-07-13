"""Controller-owned orchestration for the verified interaction architecture."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, TypeVar

from murder.llm.harness_control.adapters.base import (
    HarnessActionAdapter,
    HarnessObservationAdapter,
)
from murder.llm.harness_control.model.actions import EmissionBatchResult, SemanticAction
from murder.llm.harness_control.model.evidence import EvidenceEnvelope, TerminalFrame
from murder.llm.harness_control.model.observations import ObservationDelta, ObservationSnapshot
from murder.llm.harness_control.model.operations import (
    ActionExpectation,
    ActionRecord,
    ControllerDecision,
    ControllerDecisionKind,
    DecisionRecord,
    OperationStatus,
    OperationWarning,
    ReconciliationResult,
)
from murder.llm.harness_control.runtime.actuator import HarnessActuator, IntentPriority
from murder.llm.harness_control.runtime.observer import EvidenceHistory, ObservationStore

OperationT = TypeVar("OperationT")


class HarnessControlJournal(Protocol):
    """Durable control-store ordering required by the runtime.

    Implementations must atomically make the action and all lowered effects
    durable before `record_emission` can be called.
    """

    async def record_frame(self, frame: TerminalFrame) -> None: ...

    async def record_evidence(self, evidence: Sequence[EvidenceEnvelope]) -> None: ...

    async def record_snapshot(self, snapshot: ObservationSnapshot) -> None: ...

    async def record_delta(
        self, snapshot: ObservationSnapshot, delta: ObservationDelta
    ) -> None: ...

    async def record_operation(self, operation: object, snapshot: ObservationSnapshot) -> None: ...

    async def record_decision(self, decision: DecisionRecord) -> None: ...

    async def prepare_action(self, action: ActionRecord) -> None: ...

    async def record_transition(
        self,
        operation: object,
        snapshot: ObservationSnapshot,
        decision: DecisionRecord,
        action: ActionRecord | None,
    ) -> None: ...

    async def record_emission(self, action: ActionRecord, result: EmissionBatchResult) -> None: ...


class HarnessController:
    """Coordinates parsing, decisions, persistence, lowering, and emission.

    The class does not know harness kind or terminal syntax.  It also does not
    claim a semantic operation succeeded after emission; callers must feed a
    later frame through ``ingest_frame`` and reconcile again.
    """

    def __init__(
        self,
        observation_adapter: HarnessObservationAdapter,
        action_adapter: HarnessActionAdapter,
        observation_store: ObservationStore,
        actuator: HarnessActuator,
        journal: HarnessControlJournal,
        *,
        initial_evidence: Sequence[EvidenceEnvelope] = (),
    ) -> None:
        self._observation_adapter = observation_adapter
        self._action_adapter = action_adapter
        self._observations = observation_store
        self._actuator = actuator
        self._journal = journal
        self._evidence = EvidenceHistory()
        self._evidence.append(tuple(initial_evidence))
        self._ingest_lock = asyncio.Lock()
        self._latest_frame: TerminalFrame | None = None
        self._latest_frame_evidence: tuple[EvidenceEnvelope, ...] = ()
        self._effect_boundaries: dict[str, asyncio.Event] = {}

    @property
    def snapshot(self) -> ObservationSnapshot:
        return self._observations.latest

    def evidence_for_frame(self, frame: TerminalFrame) -> tuple[EvidenceEnvelope, ...]:
        """Expose the retained evidence belonging to one already-ingested frame."""

        return self._evidence.for_frame(frame.frame_id)

    def latest_frame_bundle(
        self,
    ) -> tuple[TerminalFrame, ObservationSnapshot, tuple[EvidenceEnvelope, ...]] | None:
        if self._latest_frame is None:
            return None
        return self._latest_frame, self.snapshot, self._latest_frame_evidence

    async def persist_operation(self, operation: object) -> None:
        """Establish durable semantic work before its first observation await."""

        await self._journal.record_operation(operation, self.snapshot)

    async def ingest_frame(self, frame: TerminalFrame) -> ObservationSnapshot:
        """Durably retain raw/evidence before exposing a projected snapshot."""

        async with self._ingest_lock:
            await self._journal.record_frame(frame)
            evidence = tuple(
                self._observation_adapter.parse_evidence(frame, self._evidence.all())
            )
            await self._journal.record_evidence(evidence)
            current_revision = self.snapshot.revision
            frame_revision = (frame.pane_epoch, frame.capture_sequence)
            current_frame_revision = (
                current_revision.pane_epoch,
                current_revision.capture_sequence,
            )
            if frame_revision <= current_frame_revision:
                # Concurrent capture callers may finish ingestion out of order.
                # The raw frame and its broad evidence remain durable for later
                # reprocessing, but stale evidence cannot replace current truth
                # or contaminate the ordered parser history.
                return self.snapshot
            self._evidence.append(evidence)
            delta = self._observation_adapter.project_observations(evidence, self.snapshot)
            snapshot = self._observations.apply(
                delta,
                captured_at=frame.captured_at,
                pane_epoch=frame.pane_epoch,
                capture_sequence=frame.capture_sequence,
            )
            await self._journal.record_snapshot(snapshot)
            await self._journal.record_delta(snapshot, delta)
            self._latest_frame = frame
            self._latest_frame_evidence = evidence
            return snapshot

    async def reconcile_once(
        self,
        operation: OperationT,
        reconcile: Callable[[OperationT, ObservationSnapshot, datetime], ControllerDecision],
        *,
        phase_name: str,
        advance: Callable[
            [OperationT, ControllerDecision, ObservationSnapshot, datetime], OperationT
        ],
        priority: IntentPriority = IntentPriority.PROMPT_SUBMISSION,
        decided_at: datetime | None = None,
    ) -> ReconciliationResult:
        """Durably advance a decision, then prepare and emit an explicit action.

        The capability reducer returns the operation state selected from the
        current observation.  This state is durable before lowering or terminal
        I/O, which is the required crash boundary for unsafe effects.
        """

        snapshot = self.snapshot
        await self._journal.record_operation(operation, snapshot)
        decided_at = decided_at or datetime.now(timezone.utc)
        decision = reconcile(operation, snapshot, decided_at)
        advanced = advance(operation, decision, snapshot, decided_at)
        operation_id = _operation_id(operation)
        decision_record = DecisionRecord(
            operation_id=operation_id,
            observation_revision=snapshot.revision,
            phase_before=phase_name,
            predicate_results=decision.predicates,
            selected_decision=decision.kind,
            selected_action_id=decision.action.action_id if decision.action else None,
            reason=decision.reason,
            decided_at=decided_at,
        )
        action_record = (
            self._action_record(decision.action, snapshot)
            if decision.kind is ControllerDecisionKind.EMIT_ACTION
            else None
        )
        if decision.kind is ControllerDecisionKind.EMIT_ACTION:
            assert action_record is not None
            boundary = asyncio.Event()
            self._effect_boundaries[operation_id] = boundary
            try:
                await self._journal.record_transition(
                    advanced, snapshot, decision_record, action_record
                )
                emission = await self._execute_action_record(action_record, priority=priority)
            finally:
                boundary.set()
                self._effect_boundaries.pop(operation_id, None)
            return ReconciliationResult(advanced, decision, emission)
        await self._journal.record_transition(advanced, snapshot, decision_record, None)
        return ReconciliationResult(advanced, decision)

    async def persist_preemption(
        self, operation: object, *, preempted_by: str, decided_at: datetime
    ) -> object:
        """Persist cancellation provenance before an interrupt receives the lease."""

        envelope = operation.envelope
        boundary = self._effect_boundaries.get(str(envelope.operation_id))
        if boundary is not None:
            await boundary.wait()
        unsafe_ambiguity = bool(envelope.action_history)
        warning = OperationWarning(
            (
                "preempted_with_unverified_effect"
                if unsafe_ambiguity
                else "preempted_by_user_interrupt"
            ),
            (
                f"preempted by semantic operation {preempted_by} after an action; "
                "terminal acceptance did not establish semantic acknowledgment"
                if unsafe_ambiguity
                else f"preempted by semantic operation {preempted_by}"
            ),
        )
        phase = getattr(type(envelope.phase), "ESCALATED", envelope.phase)
        cancelled_envelope = replace(
            envelope,
            status=(OperationStatus.ESCALATED if unsafe_ambiguity else OperationStatus.CANCELLED),
            phase=phase,
            updated_at=decided_at,
            last_observation_revision=self.snapshot.revision,
            warnings=(*envelope.warnings, warning),
        )
        cancelled = replace(operation, envelope=cancelled_envelope)
        await self._journal.record_transition(
            cancelled,
            self.snapshot,
            DecisionRecord(
                operation_id=envelope.operation_id,
                observation_revision=self.snapshot.revision,
                phase_before=envelope.phase.name,
                predicate_results=(),
                selected_decision=ControllerDecisionKind.ESCALATE,
                selected_action_id=None,
                reason=warning.message,
                decided_at=decided_at,
            ),
            None,
        )
        return cancelled

    def _action_record(
        self,
        action: SemanticAction,
        snapshot: ObservationSnapshot,
    ) -> ActionRecord:
        effects = tuple(self._action_adapter.lower(action, snapshot))
        return ActionRecord(
            action_id=action.action_id,
            operation_id=action.operation_id,
            semantic_action=action,
            lowered_effects=effects,
            selected_from_revision=snapshot.revision,
            requested_at=datetime.now(timezone.utc),
            expectation=ActionExpectation(require_revision_after=snapshot.revision),
        )

    async def _execute_action_record(
        self, record: ActionRecord, *, priority: IntentPriority
    ) -> EmissionBatchResult:
        result = await self._actuator.emit(
            record.operation_id, record.lowered_effects, priority=priority
        )
        await self._journal.record_emission(record, result)
        return result


def _operation_id(operation: object) -> str:
    envelope = getattr(operation, "envelope", None)
    operation_id = getattr(envelope, "operation_id", None)
    if not isinstance(operation_id, str):
        raise TypeError("verified controller operations must expose envelope.operation_id")
    return operation_id
