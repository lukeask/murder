"""Construction root for one verified harness-control session.

The session object is intentionally independent from the legacy procedural
``HarnessSession`` facade.  It owns the new frame→evidence→observation
runtime and provides capability entry points that never let callers reach
tmux or an adapter directly.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from murder.llm.harness_control.adapters.antigravity import AntigravityHarnessAdapter
from murder.llm.harness_control.adapters.base import (
    HarnessActionAdapter,
    HarnessObservationAdapter,
)
from murder.llm.harness_control.adapters.claude_code import ClaudeCodeAdapter
from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.adapters.cursor import CursorHarnessAdapter
from murder.llm.harness_control.adapters.pi import PiHarnessAdapter
from murder.llm.harness_control.capabilities.model_discovery import (
    DiscoverModelsOperation,
    DiscoverModelsResult,
    advance_model_discovery,
    reconcile_model_discovery,
)
from murder.llm.harness_control.capabilities.model_selection import (
    ModelTarget,
    SelectModelOperation,
    SelectModelResult,
    advance_model_selection,
    reconcile_model_selection,
)
from murder.llm.harness_control.capabilities.permissions import (
    AnswerPermissionOperation,
    AnswerPermissionPhase,
    PermissionAnswerRequest,
    advance_answer_permission,
    reconcile_answer_permission,
)
from murder.llm.harness_control.capabilities.questions import (
    AnswerQuestionOperation,
    AnswerQuestionPhase,
    QuestionAnswerRequest,
    advance_answer_question,
    reconcile_answer_question,
)
from murder.llm.harness_control.capabilities.restoration import (
    InterruptOperation,
    InterruptPhase,
    InterruptRequest,
    RestorationPhase,
    RestoreComposerOperation,
    RestoreComposerRequest,
    advance_interrupt,
    advance_restore_composer,
    reconcile_interrupt,
    reconcile_restore_composer,
)
from murder.llm.harness_control.capabilities.resume import (
    ConfigureResumeOperation,
    ConfigureResumePhase,
    OpenResumeOperation,
    OpenResumePhase,
    OpenResumeRequest,
    ResumePickerTarget,
    advance_configure_resume,
    advance_open_resume,
    reconcile_configure_resume,
    reconcile_open_resume,
)
from murder.llm.harness_control.capabilities.session_settings import (
    ConfigureSessionSettingsOperation,
    SessionSettingsPhase,
    SessionSettingsTarget,
    advance_session_settings,
    reconcile_session_settings,
)
from murder.llm.harness_control.capabilities.submit_prompt import (
    advance_submit_prompt,
    reconcile_submit_prompt,
)
from murder.llm.harness_control.capabilities.usage import (
    UsageOperation,
    UsageRequest,
    advance_usage,
    reconcile_usage,
)
from murder.llm.harness_control.model.actions import InputChunk
from murder.llm.harness_control.model.evidence import EvidenceEnvelope, HarnessId, TerminalFrame
from murder.llm.harness_control.model.observations import (
    ObservationSnapshot,
    UsageState,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import (
    ControllerDecision,
    ControllerDecisionKind,
    OperationEnvelope,
    OperationStatus,
    SubmitPromptOperation,
    SubmitPromptResult,
)
from murder.llm.harness_control.runtime.actuator import HarnessActuator, IntentPriority
from murder.llm.harness_control.runtime.controller import HarnessController
from murder.llm.harness_control.runtime.model_discovery_driver import VerifiedModelDiscoveryDriver
from murder.llm.harness_control.runtime.model_driver import (
    DEFAULT_MODEL_SELECTION_DEADLINE,
    VerifiedModelSelectionDriver,
)
from murder.llm.harness_control.runtime.observer import ObservationStore
from murder.llm.harness_control.runtime.operation_arbiter import SessionOperationArbiter
from murder.llm.harness_control.runtime.prompt_driver import (
    PromptDriverPolicy,
    VerifiedPromptDriver,
)
from murder.llm.harness_control.runtime.recovery import (
    RecoveryDecodeError,
    RecoveryDisposition,
    load_recovery_plans,
    reconstruct_persisted_operation,
)
from murder.llm.harness_control.runtime.sqlite_journal import SqliteHarnessControlJournal
from murder.llm.harness_control.runtime.tmux_frame_observer import TmuxFrameObserver
from murder.llm.harness_control.runtime.tmux_transport import TmuxTerminalEffectTransport
from murder.llm.harness_control.runtime.usage_driver import (
    UsageCollectionOutcome,
    VerifiedUsageDriver,
)
from murder.llm.harnesses.models import (
    HarnessUsageFreshness,
    HarnessUsageStatus,
    HarnessUsageTotals,
    HarnessUsageWindow,
)
from murder.state.persistence.harness_control import (
    escalate_recovery_candidate,
    get_operation,
    latest_observation_snapshot,
    list_session_evidence,
)

PARSER_HISTORY_FRAME_LIMIT = 64


@dataclass(frozen=True, slots=True)
class IngestedFrame:
    """One immutable capture after durable evidence projection."""

    frame: TerminalFrame
    snapshot: ObservationSnapshot
    evidence: tuple[EvidenceEnvelope, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuredDecisionTimingPolicy:
    """Clock and pacing boundary for verified question and permission replies.

    The operation carries the authoritative absolute deadline.  This policy
    only supplies the mechanics needed to observe until that deadline, and is
    injectable so deadline behavior is deterministic in tests.
    """

    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    observation_interval: timedelta = timedelta(milliseconds=250)

    def __post_init__(self) -> None:
        if self.observation_interval <= timedelta():
            raise ValueError("structured-decision observation interval must be positive")


DEFAULT_STRUCTURED_DECISION_TIMING_POLICY = StructuredDecisionTimingPolicy()


class VerifiedHarnessControlSession:
    """The only owner of verified control components for a harness pane."""

    def __init__(
        self,
        controller: HarnessController,
        prompt_driver: VerifiedPromptDriver,
        observer: TmuxFrameObserver,
        model_driver: VerifiedModelSelectionDriver,
        usage_driver: VerifiedUsageDriver,
        *,
        model_discovery_driver: VerifiedModelDiscoveryDriver | None = None,
        harness_id: HarnessId,
        terminal_session: str,
        connection: sqlite3.Connection,
        persistence_session_id: str | None,
        operation_arbiter: SessionOperationArbiter | None = None,
        structured_decision_timing: StructuredDecisionTimingPolicy = (
            DEFAULT_STRUCTURED_DECISION_TIMING_POLICY
        ),
    ) -> None:
        self.controller = controller
        self._prompt_driver = prompt_driver
        self._observer = observer
        self._model_driver = model_driver
        self._model_discovery_driver = model_discovery_driver or VerifiedModelDiscoveryDriver(
            controller, observer
        )
        self._usage_driver = usage_driver
        self._connection = connection
        self._persistence_session_id = persistence_session_id
        self._operation_arbiter = operation_arbiter or SessionOperationArbiter()
        self._structured_decision_timing = structured_decision_timing
        self.harness_id = harness_id
        self.terminal_session = terminal_session

    @classmethod
    def from_tmux(
        cls,
        *,
        harness_kind: str,
        terminal_session: str,
        connection: sqlite3.Connection,
        persistence_session_id: str | None = None,
        pane_epoch: int = 0,
        observation_adapter: HarnessObservationAdapter | None = None,
        action_adapter: HarnessActionAdapter | None = None,
        prompt_policy: PromptDriverPolicy | None = None,
        prompt_sleep: Callable[[float], Awaitable[None]] | None = None,
        structured_decision_timing: StructuredDecisionTimingPolicy = (
            DEFAULT_STRUCTURED_DECISION_TIMING_POLICY
        ),
    ) -> VerifiedHarnessControlSession:
        """Assemble the one real observation/controller/actuator path.

        Supplying adapters is useful for tests and explicitly supports future
        parser-version dispatch.  Production callers otherwise select the
        one concrete verified adapter registered for the harness kind.
        """

        harness_id = HarnessId(harness_kind)
        observation_adapter = observation_adapter or _adapter_for(harness_kind, connection)
        action_adapter = action_adapter or observation_adapter
        if not hasattr(action_adapter, "lower"):
            raise TypeError(f"verified adapter for {harness_kind!r} cannot lower semantic actions")
        captured_at = datetime.now(timezone.utc)
        persisted_snapshot = latest_observation_snapshot(
            connection,
            harness_id=str(harness_id),
            session_id=persistence_session_id,
        )
        initial_snapshot = persisted_snapshot or unknown_snapshot(
            harness_id, captured_at=captured_at
        )
        initial_evidence = list_session_evidence(
            connection,
            harness_id=str(harness_id),
            session_id=persistence_session_id,
            frame_limit=PARSER_HISTORY_FRAME_LIMIT,
        )
        effective_pane_epoch = pane_epoch
        initial_capture_sequence = 0
        if persisted_snapshot is not None:
            effective_pane_epoch = max(pane_epoch, persisted_snapshot.revision.pane_epoch)
            if effective_pane_epoch == persisted_snapshot.revision.pane_epoch:
                initial_capture_sequence = persisted_snapshot.revision.capture_sequence
        controller = HarnessController(
            observation_adapter,
            action_adapter,
            ObservationStore(initial_snapshot),
            HarnessActuator(TmuxTerminalEffectTransport(terminal_session)),
            SqliteHarnessControlJournal(connection, session_id=persistence_session_id),
            initial_evidence=initial_evidence,
        )
        observer = TmuxFrameObserver(
            terminal_session,
            harness_id,
            pane_epoch=effective_pane_epoch,
            capture_sequence=initial_capture_sequence,
        )
        return cls(
            controller,
            VerifiedPromptDriver(
                controller,
                observer,
                **({"policy": prompt_policy} if prompt_policy is not None else {}),
                **({"sleep": prompt_sleep} if prompt_sleep is not None else {}),
            ),
            observer,
            VerifiedModelSelectionDriver(controller, observer),
            VerifiedUsageDriver(controller, observer),
            model_discovery_driver=VerifiedModelDiscoveryDriver(controller, observer),
            harness_id=harness_id,
            terminal_session=terminal_session,
            connection=connection,
            persistence_session_id=persistence_session_id,
            structured_decision_timing=structured_decision_timing,
        )

    async def submit_prompt(
        self,
        chunks: tuple[InputChunk, ...],
        *,
        await_completion: bool = False,
        submission_deadline: timedelta = timedelta(seconds=60),
        completion_deadline: timedelta | None = None,
    ) -> SubmitPromptResult:
        operation = self._prompt_driver.create_operation(
            chunks,
            await_completion=await_completion,
            submission_deadline=submission_deadline,
            completion_deadline=completion_deadline,
        )
        operation_id = operation.envelope.operation_id
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation_id,
            IntentPriority.PROMPT_SUBMISSION,
            lambda: self._prompt_driver.resume(operation),
            on_preempt=self._preemption_hook(operation_id),
        )

    async def observe_once(self):
        """Persist one raw frame, broad evidence, and projected observation."""

        return (await self.ingest_once()).snapshot

    async def ingest_once(self) -> IngestedFrame:
        """Capture once and expose that same persisted frame to read-only consumers."""

        frame = await self._observer.capture_frame()
        await self.controller.ingest_frame(frame)
        bundle = self.controller.latest_frame_bundle()
        if bundle is None:
            raise RuntimeError("verified controller accepted no frame during ingestion")
        accepted_frame, snapshot, evidence = bundle
        return IngestedFrame(accepted_frame, snapshot, evidence)

    async def recover_pending_operations(self) -> tuple[str, ...]:
        """Reconstruct persisted state and reconcile it with one fresh frame.

        The persisted operation—not a vanished Python call stack—selects the
        capability reducer.  An unsafe prior emission may be verified from the
        new frame, but recovery will never permit that reducer to emit another
        action automatically.
        """

        return await self._operation_arbiter.run(
            f"recovery:{uuid4()}",
            IntentPriority.PERMISSION_RESPONSE,
            self._recover_pending_operations,
        )

    async def _recover_pending_operations(self) -> tuple[str, ...]:
        snapshot = await self.observe_once()
        plans = load_recovery_plans(
            self._connection,
            harness_id=str(self.harness_id),
            session_id=self._persistence_session_id,
        )
        for plan in plans:
            try:
                operation = reconstruct_persisted_operation(
                    plan.candidate.operation, actions=plan.candidate.actions
                )
                reconcile, advance, priority = _recovery_contract(operation)
            except (RecoveryDecodeError, TypeError) as exc:
                _escalate_recovery(
                    self._connection,
                    plan.operation_id,
                    f"persisted operation cannot be reconstructed: {exc}",
                    snapshot.captured_at,
                )
                continue

            if plan.disposition is RecoveryDisposition.AMBIGUOUS_UNSAFE_EFFECT:
                preview = reconcile(operation, snapshot, snapshot.captured_at)
                if preview.kind is ControllerDecisionKind.EMIT_ACTION:
                    await self._record_recovery_escalation(
                        operation,
                        advance,
                        priority,
                        snapshot,
                        "unsafe emitted operation requested another action after restart",
                    )
                    continue

            current = operation
            for _ in range(120):
                result = await self.controller.reconcile_once(
                    current,
                    reconcile,
                    phase_name=current.envelope.phase.name,
                    advance=advance,
                    priority=priority,
                    decided_at=snapshot.captured_at,
                )
                current = result.operation
                if result.decision.kind in {
                    ControllerDecisionKind.SUCCEED,
                    ControllerDecisionKind.FAIL,
                    ControllerDecisionKind.ESCALATE,
                }:
                    break
                if plan.disposition is RecoveryDisposition.AMBIGUOUS_UNSAFE_EFFECT:
                    await self._record_recovery_escalation(
                        current,
                        advance,
                        priority,
                        snapshot,
                        "fresh restart observation did not resolve the unsafe emitted operation",
                    )
                    break
                snapshot = await self.observe_once()
            else:
                await self._record_recovery_escalation(
                    current,
                    advance,
                    priority,
                    snapshot,
                    "restart recovery observation budget exhausted",
                )
        self._connection.commit()
        return tuple(plan.operation_id for plan in plans)

    async def _record_recovery_escalation(
        self,
        operation: object,
        advance: Callable,
        priority: IntentPriority,
        snapshot: ObservationSnapshot,
        reason: str,
    ) -> None:
        """Close reconstructable work through its typed capability transition."""

        phase = operation.envelope.phase
        escalated_phase = getattr(type(phase), "ESCALATED", phase)

        def escalate(_operation, _snapshot, _now):
            return ControllerDecision(
                ControllerDecisionKind.ESCALATE,
                escalated_phase,
                None,
                reason,
            )

        await self.controller.reconcile_once(
            operation,
            escalate,
            phase_name=phase.name,
            advance=advance,
            priority=priority,
            decided_at=snapshot.captured_at,
        )

    async def select_model(
        self,
        target: ModelTarget,
        *,
        deadline: timedelta = DEFAULT_MODEL_SELECTION_DEADLINE,
    ) -> SelectModelResult:
        """Configure and activate a model only after independent readback."""

        operation = self._model_driver.create_operation(target, deadline=deadline)
        operation_id = operation.envelope.operation_id
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation_id,
            IntentPriority.MODEL_SELECTION,
            lambda: self._model_driver.resume(operation),
            on_preempt=self._preemption_hook(operation_id),
        )

    async def discover_models(
        self, *, deadline: timedelta = timedelta(minutes=2)
    ) -> DiscoverModelsResult:
        """Read every interactive ``/model`` row by traversing the live picker."""

        operation_id = str(uuid4())
        return await self._operation_arbiter.run(
            operation_id,
            IntentPriority.MODEL_SELECTION,
            lambda: self._model_discovery_driver.discover(deadline=deadline),
            on_preempt=None,
        )

    async def collect_usage(self, *, trigger: str) -> HarnessUsageStatus | None:
        """Collect a fresh terminal usage observation through the actuator.

        ``trigger`` is retained as persistence context by the caller; it does
        not alter controller policy or lower directly to terminal syntax.
        """
        del trigger
        operation = self._usage_driver.create_operation(
            UsageRequest(timedelta(minutes=1), True, None)
        )
        operation_id = operation.envelope.operation_id
        await self.controller.persist_operation(operation)
        result = await self._operation_arbiter.run(
            operation_id,
            IntentPriority.BACKGROUND_USAGE,
            lambda: self._usage_driver.resume(operation),
            on_preempt=self._preemption_hook(operation_id),
        )
        if result.outcome is not UsageCollectionOutcome.COLLECTED or result.usage is None:
            return None
        return _as_harness_usage_status(str(self.harness_id), result.usage)

    async def configure_settings(
        self,
        target: SessionSettingsTarget,
        *,
        deadline: timedelta = timedelta(minutes=1),
    ) -> bool:
        """Set run/fast modes and require a fresh live-chrome readback."""

        _validate_structured_decision_deadline(deadline, "session settings")
        now = self._structured_decision_timing.clock()
        operation = ConfigureSessionSettingsOperation(
            OperationEnvelope(
                str(uuid4()),
                "configure_session_settings",
                OperationStatus.PENDING,
                SessionSettingsPhase.CREATED,
                now,
                now,
                now + deadline,
            ),
            target,
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.MODEL_SELECTION,
            lambda: self._drive_structured(
                operation,
                reconcile_session_settings,
                advance_session_settings,
                IntentPriority.MODEL_SELECTION,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    async def answer_question(
        self,
        request: QuestionAnswerRequest,
        *,
        deadline: timedelta = timedelta(minutes=2),
        operation_id: str | None = None,
    ) -> bool:
        """Execute one recorded external question decision through fresh evidence."""

        _validate_structured_decision_deadline(deadline, "question answer")
        now = self._structured_decision_timing.clock()
        operation = AnswerQuestionOperation(
            OperationEnvelope(
                operation_id or str(uuid4()), "answer_question", OperationStatus.PENDING,
                AnswerQuestionPhase.CREATED, now, now, now + deadline,
            ),
            request,
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.PROMPT_SUBMISSION,
            lambda: self._drive_structured(
                operation,
                reconcile_answer_question,
                advance_answer_question,
                IntentPriority.PROMPT_SUBMISSION,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    async def open_resume_picker(
        self, *, deadline: timedelta = timedelta(minutes=1)
    ) -> bool:
        """Open and verify the interactive saved-session picker."""

        _validate_structured_decision_deadline(deadline, "resume picker")
        now = self._structured_decision_timing.clock()
        operation = OpenResumeOperation(
            OperationEnvelope(
                str(uuid4()),
                "open_resume_picker",
                OperationStatus.PENDING,
                OpenResumePhase.CREATED,
                now,
                now,
                now + deadline,
            ),
            OpenResumeRequest(deadline),
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.PROMPT_SUBMISSION,
            lambda: self._drive_structured(
                operation,
                reconcile_open_resume,
                advance_open_resume,
                IntentPriority.PROMPT_SUBMISSION,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    async def configure_resume_picker(
        self,
        target: ResumePickerTarget,
        *,
        deadline: timedelta = timedelta(minutes=1),
    ) -> bool:
        """Reset, reopen, configure, and verify Codex's resume picker."""

        _validate_structured_decision_deadline(deadline, "resume configuration")
        if not await self.restore_composer(deadline=deadline):
            return False
        if not await self.open_resume_picker(deadline=deadline):
            return False
        now = self._structured_decision_timing.clock()
        operation = ConfigureResumeOperation(
            OperationEnvelope(
                str(uuid4()),
                "configure_resume_picker",
                OperationStatus.PENDING,
                ConfigureResumePhase.CREATED,
                now,
                now,
                now + deadline,
            ),
            target,
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.PROMPT_SUBMISSION,
            lambda: self._drive_structured(
                operation,
                reconcile_configure_resume,
                advance_configure_resume,
                IntentPriority.PROMPT_SUBMISSION,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    async def restore_composer(
        self, *, deadline: timedelta = timedelta(seconds=20)
    ) -> bool:
        """Dismiss the current typed overlay and verify an actionable composer."""

        _validate_structured_decision_deadline(deadline, "composer restoration")
        now = self._structured_decision_timing.clock()
        operation = RestoreComposerOperation(
            OperationEnvelope(
                str(uuid4()),
                "restore_composer",
                OperationStatus.PENDING,
                RestorationPhase.CREATED,
                now,
                now,
                now + deadline,
            ),
            RestoreComposerRequest(deadline),
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.PROMPT_SUBMISSION,
            lambda: self._drive_structured(
                operation,
                reconcile_restore_composer,
                advance_restore_composer,
                IntentPriority.PROMPT_SUBMISSION,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    async def answer_permission(
        self,
        request: PermissionAnswerRequest,
        *,
        deadline: timedelta = timedelta(minutes=2),
        operation_id: str | None = None,
    ) -> bool:
        """Execute one recorded permission decision; approvals are never replayed."""

        _validate_structured_decision_deadline(deadline, "permission answer")
        now = self._structured_decision_timing.clock()
        operation = AnswerPermissionOperation(
            OperationEnvelope(
                operation_id or str(uuid4()), "answer_permission", OperationStatus.PENDING,
                AnswerPermissionPhase.CREATED, now, now, now + deadline,
            ),
            request,
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.PERMISSION_RESPONSE,
            lambda: self._drive_structured(
                operation,
                reconcile_answer_permission,
                advance_answer_permission,
                IntentPriority.PERMISSION_RESPONSE,
            ),
            on_preempt=self._preemption_hook(operation.envelope.operation_id),
        )

    def _preemption_hook(self, operation_id: str) -> Callable[[str], Awaitable[None]]:
        async def persist(preempted_by: str) -> None:
            stored = get_operation(self._connection, operation_id)
            if stored is None:
                raise RuntimeError(
                    f"operation {operation_id!r} cannot be preempted before durable creation"
                )
            operation = reconstruct_persisted_operation(stored)
            await self.controller.persist_preemption(
                operation,
                preempted_by=preempted_by,
                decided_at=datetime.now(timezone.utc),
            )

        return persist

    async def _drive_structured(
        self, operation, reconcile, advance, priority: IntentPriority
    ) -> bool:
        """Shared fresh-observation loop for non-replayable structured decisions."""

        deadline = operation.envelope.deadline
        if deadline is None:
            raise ValueError("structured-decision operation requires an absolute deadline")

        while self._structured_decision_timing.clock() < deadline:
            await self.observe_once()
            result = await self.controller.reconcile_once(
                operation, reconcile, phase_name=operation.envelope.phase.name,
                advance=advance,
                priority=priority,
                decided_at=self._structured_decision_timing.clock(),
            )
            operation = result.operation
            if result.decision.kind is ControllerDecisionKind.SUCCEED:
                return True
            if result.decision.kind in {
                ControllerDecisionKind.FAIL,
                ControllerDecisionKind.ESCALATE,
            }:
                return False
            remaining = deadline - self._structured_decision_timing.clock()
            if remaining <= timedelta():
                break
            sleep_for = min(
                self._structured_decision_timing.observation_interval,
                remaining,
            )
            await self._structured_decision_timing.sleep(sleep_for.total_seconds())

        # A final capture/reconciliation at the absolute deadline converts the
        # reducer's timeout decision into a durable terminal transition.
        await self.observe_once()
        result = await self.controller.reconcile_once(
            operation,
            reconcile,
            phase_name=operation.envelope.phase.name,
            advance=advance,
            priority=priority,
            decided_at=self._structured_decision_timing.clock(),
        )
        return result.decision.kind is ControllerDecisionKind.SUCCEED

    async def interrupt(self, *, deadline: timedelta = timedelta(seconds=20)) -> bool:
        """Verify a generation interruption from fresh evidence; never resend it."""

        now = datetime.now(timezone.utc)
        operation = InterruptOperation(
            OperationEnvelope(
                operation_id=str(uuid4()),
                capability="interrupt",
                status=OperationStatus.PENDING,
                phase=InterruptPhase.CREATED,
                created_at=now,
                updated_at=now,
                deadline=now + deadline,
            ),
            InterruptRequest(deadline),
        )
        await self.controller.persist_operation(operation)
        return await self._operation_arbiter.run(
            operation.envelope.operation_id,
            IntentPriority.USER_INTERRUPT,
            lambda: self._drive_interrupt(operation),
            preempt_active=True,
        )

    async def _drive_interrupt(self, operation: InterruptOperation) -> bool:
        # A bounded loop is an observation deadline, not a retry loop: after
        # the one recorded interrupt action, reconciliation only observes or
        # escalates.
        for _ in range(80):
            await self.observe_once()
            result = await self.controller.reconcile_once(
                operation,
                reconcile_interrupt,
                phase_name=operation.envelope.phase.name,
                advance=advance_interrupt,
                priority=IntentPriority.USER_INTERRUPT,
            )
            operation = result.operation  # type: ignore[assignment]
            if result.decision.kind.name == "SUCCEED":
                return True
            if result.decision.kind.name in {"ESCALATE", "FAIL"}:
                return False
            await asyncio.sleep(0.25)
        result = await self.controller.reconcile_once(
            operation,
            _interrupt_observation_budget_exhausted,
            phase_name=operation.envelope.phase.name,
            advance=advance_interrupt,
            priority=IntentPriority.USER_INTERRUPT,
        )
        del result
        return False


def _interrupt_observation_budget_exhausted(
    _operation: InterruptOperation, _snapshot: object, _now: datetime
) -> ControllerDecision:
    return ControllerDecision(
        ControllerDecisionKind.ESCALATE,
        InterruptPhase.ESCALATED,
        None,
        "interrupt observation budget exhausted without acknowledgment",
    )


def _validate_structured_decision_deadline(deadline: timedelta, label: str) -> None:
    if deadline <= timedelta():
        raise ValueError(f"{label} deadline must be positive")


def _recovery_contract(operation: object):
    """Return the reducer contract for one allowlisted reconstructed root."""

    contracts = (
        (
            SubmitPromptOperation,
            reconcile_submit_prompt,
            advance_submit_prompt,
            IntentPriority.PROMPT_SUBMISSION,
        ),
        (
            SelectModelOperation,
            reconcile_model_selection,
            advance_model_selection,
            IntentPriority.MODEL_SELECTION,
        ),
        (
            DiscoverModelsOperation,
            reconcile_model_discovery,
            advance_model_discovery,
            IntentPriority.MODEL_SELECTION,
        ),
        (
            OpenResumeOperation,
            reconcile_open_resume,
            advance_open_resume,
            IntentPriority.PROMPT_SUBMISSION,
        ),
        (
            ConfigureResumeOperation,
            reconcile_configure_resume,
            advance_configure_resume,
            IntentPriority.PROMPT_SUBMISSION,
        ),
        (
            AnswerQuestionOperation,
            reconcile_answer_question,
            advance_answer_question,
            IntentPriority.PROMPT_SUBMISSION,
        ),
        (
            ConfigureSessionSettingsOperation,
            reconcile_session_settings,
            advance_session_settings,
            IntentPriority.MODEL_SELECTION,
        ),
        (
            AnswerPermissionOperation,
            reconcile_answer_permission,
            advance_answer_permission,
            IntentPriority.PERMISSION_RESPONSE,
        ),
        (
            RestoreComposerOperation,
            reconcile_restore_composer,
            advance_restore_composer,
            IntentPriority.PROMPT_SUBMISSION,
        ),
        (
            InterruptOperation,
            reconcile_interrupt,
            advance_interrupt,
            IntentPriority.USER_INTERRUPT,
        ),
        (UsageOperation, reconcile_usage, advance_usage, IntentPriority.BACKGROUND_USAGE),
    )
    for operation_type, reconcile, advance, priority in contracts:
        if isinstance(operation, operation_type):
            return reconcile, advance, priority
    raise TypeError(f"unsupported recovered operation root {type(operation).__name__}")


def _escalate_recovery(
    connection: sqlite3.Connection,
    operation_id: str,
    reason: str,
    observed_at: datetime,
) -> None:
    escalate_recovery_candidate(
        connection,
        operation_id=operation_id,
        reason=reason,
        observed_at=observed_at,
    )


def _adapter_for(
    harness_kind: str, connection: sqlite3.Connection | None = None
) -> HarnessObservationAdapter:
    # Imports remain at this composition root so model/runtime layers never
    # import concrete adapters.  A missing adapter is explicit, rather than a
    # fallback to a legacy procedural sender.
    if harness_kind == "codex":
        return CodexHarnessAdapter()
    if harness_kind == "claude_code":
        return ClaudeCodeAdapter()
    if harness_kind == "cursor":
        return CursorHarnessAdapter(http_usage=_latest_cursor_http_usage(connection))
    if harness_kind == "antigravity":
        return AntigravityHarnessAdapter()
    if harness_kind == "pi":
        return PiHarnessAdapter()
    raise ValueError(f"no verified adapter registered for harness {harness_kind!r}")


def _latest_cursor_http_usage(connection: sqlite3.Connection | None) -> dict[str, object] | None:
    """Load persisted authoritative Cursor HTTP evidence for the adapter edge.

    The background sampler is allowed to call Cursor's HTTP endpoint, but it
    must never control a terminal pane.  This composition-root handoff turns
    that durable side-channel result into evidence on the next verified frame.
    A missing table is normal for narrow unit-test connections.
    """

    if connection is None:
        return None
    try:
        row = connection.execute(
            """
            SELECT status_json
            FROM harness_usage_snapshots
            WHERE harness = 'cursor'
            ORDER BY fetched_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    raw = row[0] if not isinstance(row, sqlite3.Row) else row["status_json"]
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _as_harness_usage_status(harness: str, usage: UsageState) -> HarnessUsageStatus:
    totals_raw = usage.session_totals or {}
    totals = HarnessUsageTotals(
        input_tokens=_int_or_none(totals_raw.get("input_tokens")),
        output_tokens=_int_or_none(totals_raw.get("output_tokens")),
        cache_read_tokens=_int_or_none(totals_raw.get("cache_read_tokens")),
        cache_write_tokens=_int_or_none(totals_raw.get("cache_write_tokens")),
        cost_usd=_float_or_none(totals_raw.get("cost_usd")),
        api_duration_s=_float_or_none(totals_raw.get("api_duration_s")),
        wall_duration_s=_float_or_none(totals_raw.get("wall_duration_s")),
        lines_added=_int_or_none(totals_raw.get("lines_added")),
        lines_removed=_int_or_none(totals_raw.get("lines_removed")),
    )
    return HarnessUsageStatus(
        harness=harness,
        source="verified_terminal",
        fetched_at=_fetched_at(),
        plan=usage.plan,
        windows=[
            HarnessUsageWindow(
                name=window.name,
                percent_used=window.percent_used,
                reset_at=window.resets_at.isoformat() if window.resets_at else window.reset_text,
            )
            for window in usage.windows
        ],
        session=totals,
        messages=[usage.advisory_text] if usage.advisory_text else [],
        freshness=(
            HarnessUsageFreshness.CURRENT
            if str(getattr(usage.freshness, "value", usage.freshness)).lower() == "current"
            else HarnessUsageFreshness.ADVISORY_STALE
            if str(getattr(usage.freshness, "value", usage.freshness)).lower()
            in {"advisory_stale", "harness_advisory_stale"}
            else HarnessUsageFreshness.UNKNOWN
        ),
        raw={
            "freshness": usage.freshness,
            "source_surface": usage.source_surface.name if usage.source_surface else None,
        },
    )


def _fetched_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


__all__ = ["VerifiedHarnessControlSession"]
