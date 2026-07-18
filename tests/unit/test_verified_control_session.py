from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.restoration import (
    InterruptOperation,
    InterruptPhase,
    InterruptRequest,
)
from murder.llm.harness_control.capabilities.usage import UsageOperation, UsagePhase, UsageRequest
from murder.llm.harness_control.model.actions import (
    DuplicatePolicy,
    EffectEmission,
    EmissionStatus,
    RequestUsage,
    SendInterrupt,
    SendNamedKey,
)
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceRef,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    GenerationPhase,
    GenerationState,
    Knowledge,
    ObservationDelta,
    ObservationRevision,
    Observed,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import (
    ActionExpectation,
    ActionRecord,
    OperationEnvelope,
    OperationStatus,
    ReconciliationResult,
)
from murder.llm.harness_control.runtime.recovery import reconstruct_persisted_operation
from murder.llm.harness_control.runtime.session import (
    PARSER_HISTORY_FRAME_LIMIT,
    VerifiedHarnessControlSession,
)
from murder.llm.harness_control.runtime.usage_driver import (
    UsageCollectionOutcome,
    UsageCollectionResult,
)
from murder.runtime.terminal import tmux
from murder.state.persistence.harness_control import (
    get_operation,
    load_recovery_candidates,
    persist_action_record,
    persist_evidence,
    persist_frame,
    persist_observation_snapshot,
    persist_operation,
    record_effect_emissions,
)
from murder.state.persistence.schema import init_db

NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)
HYDRATED_CAPTURE_SEQUENCE = 11


def test_verified_attach_hydrates_only_bounded_recent_parser_history() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    total_frames = PARSER_HISTORY_FRAME_LIMIT + 6
    for index in range(total_frames):
        frame = TerminalFrame(
            f"frame-{index}",
            "codex",
            NOW,
            120,
            40,
            f"frame {index}",
            False,
            1,
            index + 1,
        )
        evidence = EvidenceEnvelope(
            f"evidence-{index}",
            frame.frame_id,
            frame.harness_id,
            "bounded-history-test/v1",
            NOW,
            "bounded.history",
            {"index": index},
            (),
            EvidenceDiagnostics("bounded-history-test"),
        )
        persist_frame(connection, frame, session_id="long-lived-agent")
        persist_evidence(connection, evidence)

    class Adapter:
        parser_version = "bounded-history-test/v1"

        def __init__(self) -> None:
            self.history_ids: tuple[str, ...] = ()

        def parse_evidence(self, _frame, history):
            self.history_ids = tuple(str(item.evidence_id) for item in history)
            return ()

        def project_observations(self, _evidence, _prior):
            return ObservationDelta({})

        def lower(self, _action, _snapshot):
            return ()

    adapter = Adapter()
    session = VerifiedHarnessControlSession.from_tmux(
        harness_kind="codex",
        terminal_session="codex-pane",
        connection=connection,
        persistence_session_id="long-lived-agent",
        observation_adapter=adapter,
        action_adapter=adapter,
    )
    asyncio.run(
        session.controller.ingest_frame(
            TerminalFrame(
                "fresh-frame",
                "codex",
                NOW,
                120,
                40,
                "fresh",
                False,
                1,
                total_frames + 1,
            )
        )
    )

    assert len(adapter.history_ids) == PARSER_HISTORY_FRAME_LIMIT
    assert adapter.history_ids[0] == "evidence-6"
    assert adapter.history_ids[-1] == f"evidence-{total_frames - 1}"


def test_verified_control_session_composes_each_supported_harness_without_legacy_sender() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    for harness_kind in ("codex", "claude_code", "cursor", "antigravity", "pi"):
        session = VerifiedHarnessControlSession.from_tmux(
            harness_kind=harness_kind,
            terminal_session=f"{harness_kind}-pane",
            connection=conn,
            persistence_session_id="agent-1",
        )
        assert session.harness_id == harness_kind
        assert session.terminal_session == f"{harness_kind}-pane"
        assert session.controller.snapshot.harness_id == harness_kind

    requirements: list[bool] = []

    class UsageDriver:
        def create_operation(self, request):
            requirements.append(request.require_current)
            return UsageOperation(
                OperationEnvelope(
                    "usage-operation",
                    "usage",
                    OperationStatus.PENDING,
                    UsagePhase.CREATED,
                    NOW,
                    NOW,
                    NOW + request.deadline,
                ),
                request,
            )

        async def resume(self, operation):
            assert operation.envelope.operation_id == "usage-operation"
            return UsageCollectionResult(
                "usage-operation", UsageCollectionOutcome.ESCALATED, None
            )

    session._usage_driver = UsageDriver()  # type: ignore[assignment]
    assert asyncio.run(session.collect_usage(trigger="live")) is None
    assert requirements == [True]

    preemptible = UsageOperation(
        OperationEnvelope(
            "preemptible",
            "usage",
            OperationStatus.RUNNING,
            UsagePhase.AWAITING_FRESH_USAGE,
            NOW,
            NOW,
            NOW + timedelta(minutes=1),
        ),
        UsageRequest(timedelta(minutes=1), True, None),
    )
    asyncio.run(session.controller.persist_operation(preemptible))
    asyncio.run(session._preemption_hook("preemptible")("interrupt-1"))  # noqa: SLF001
    persisted = get_operation(conn, "preemptible")
    assert persisted is not None
    cancelled = reconstruct_persisted_operation(persisted)
    assert cancelled.envelope.status is OperationStatus.CANCELLED
    assert cancelled.envelope.warnings[-1].message.endswith("interrupt-1")
    assert all(
        candidate.operation.operation_id != "preemptible"
        for candidate in load_recovery_candidates(conn, harness_id="pi", session_id="agent-1")
    )

    unsafe = replace(
        preemptible,
        envelope=replace(
            preemptible.envelope,
            operation_id="unsafe-preemptible",
            action_history=("unsafe-usage-action",),
        ),
    )
    asyncio.run(session.controller.persist_operation(unsafe))
    persist_action_record(
        conn,
        ActionRecord(
            "unsafe-usage-action",
            "unsafe-preemptible",
            RequestUsage(
                "unsafe-usage-action",
                "unsafe-preemptible",
                DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
            ),
            (SendNamedKey("unsafe-usage-effect", "Enter"),),
            session.controller.snapshot.revision,
            NOW,
            ActionExpectation(session.controller.snapshot.revision),
        ),
    )
    asyncio.run(session._preemption_hook("unsafe-preemptible")("interrupt-2"))  # noqa: SLF001
    unsafe_persisted = get_operation(conn, "unsafe-preemptible")
    assert unsafe_persisted is not None
    unsafe_escalated = reconstruct_persisted_operation(unsafe_persisted)
    assert unsafe_escalated.envelope.status is OperationStatus.ESCALATED
    assert unsafe_escalated.envelope.warnings[-1].code == "preempted_with_unverified_effect"


def test_session_recovery_reconciles_typed_state_against_one_fresh_observation() -> None:
    async def scenario() -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        init_db(connection)
        baseline = ObservationRevision(0, 1, 1)
        fresh = ObservationRevision(0, 2, 2)
        operation = InterruptOperation(
            OperationEnvelope(
                "interrupt-before-restart",
                "interrupt",
                OperationStatus.RUNNING,
                InterruptPhase.AWAITING_ACKNOWLEDGMENT,
                NOW,
                NOW,
                None,
                last_observation_revision=baseline,
                action_history=("interrupt-action",),
            ),
            InterruptRequest(timedelta(seconds=20)),
            baseline,
            "interrupt-action",
        )
        persist_operation(
            connection,
            operation.envelope,
            harness_id="pi",
            session_id="agent-1",
            request=operation.request,
            operation_state=operation,
        )
        persist_action_record(
            connection,
            ActionRecord(
                "interrupt-action",
                "interrupt-before-restart",
                SendInterrupt(
                    "interrupt-action",
                    "interrupt-before-restart",
                    DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
                ),
                (SendNamedKey("interrupt-effect", "Escape"),),
                baseline,
                NOW,
                ActionExpectation(baseline),
            ),
        )
        snapshot = unknown_snapshot("pi", captured_at=NOW, revision=fresh)
        snapshot = replace(
            snapshot,
            generation=Observed.present(
                GenerationState(GenerationPhase.STOPPED, False, False, None, None, None),
                evidence=(),
                observed_at=NOW,
                revision=fresh,
            ),
        )

        class Controller:
            def __init__(self) -> None:
                self.snapshot = snapshot
                self.reconciled: list[object] = []
                self.decisions = []

            async def reconcile_once(self, current, reconcile, *, advance, **_kwargs):
                self.reconciled.append(current)
                decision = reconcile(current, self.snapshot, NOW)
                self.decisions.append(decision)
                advanced = advance(current, decision, self.snapshot, NOW)
                return ReconciliationResult(advanced, decision)

        controller = Controller()
        session = VerifiedHarnessControlSession(
            controller,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            harness_id="pi",
            terminal_session="pi-pane",
            connection=connection,
            persistence_session_id="agent-1",
        )

        observations = 0

        async def observe_once():
            nonlocal observations
            observations += 1
            return snapshot

        session.observe_once = observe_once  # type: ignore[method-assign]
        recovered = await session.recover_pending_operations()

        assert recovered == ("interrupt-before-restart",)
        assert observations == 1
        assert controller.reconciled == [operation]
        assert controller.decisions[0].kind.name == "SUCCEED"
        assert controller.decisions[0].action is None

    asyncio.run(scenario())


def test_restart_hydrates_semantic_and_evidence_baselines_then_recovers_without_replay(  # noqa: PLR0915 - end-to-end recovery trace
    monkeypatch,
) -> None:
    """Restart preserves knowledge/history and drives safe observation to quiescence."""

    async def scenario() -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        init_db(connection)
        baseline = ObservationRevision(4, 9, 3)
        frame = TerminalFrame("old-frame", "codex", NOW, 120, 40, "old", False, 4, 9)
        evidence = EvidenceEnvelope(
            "old-evidence",
            frame.frame_id,
            frame.harness_id,
            "restart-test/v1",
            NOW,
            "restart.baseline",
            {"state": "contradicted"},
            (),
            EvidenceDiagnostics("restart-test"),
        )
        ref = EvidenceRef(evidence.evidence_id, evidence.frame_id, ())
        persisted = unknown_snapshot("codex", captured_at=NOW, revision=baseline)
        persisted = replace(
            persisted,
            generation=Observed.without_value(
                Knowledge.CONTRADICTED,
                evidence=(ref,),
                observed_at=NOW,
                revision=baseline,
                explanation="persisted parser contradiction",
            ),
        )
        persist_frame(connection, frame, session_id="agent-1")
        persist_evidence(connection, evidence)
        persist_observation_snapshot(connection, persisted, session_id="agent-1")

        operation = InterruptOperation(
            OperationEnvelope(
                "z-safe-recovery",
                "interrupt",
                OperationStatus.RUNNING,
                InterruptPhase.AWAITING_ACKNOWLEDGMENT,
                NOW,
                NOW,
                None,
                last_observation_revision=baseline,
                action_history=("already-emitted",),
            ),
            InterruptRequest(timedelta(seconds=20)),
            baseline,
            "already-emitted",
        )
        persist_operation(
            connection,
            operation.envelope,
            harness_id="codex",
            session_id="agent-1",
            request=operation.request,
            operation_state=operation,
        )
        persist_action_record(
            connection,
            ActionRecord(
                "already-emitted",
                "z-safe-recovery",
                SendInterrupt(
                    "already-emitted", "z-safe-recovery", DuplicatePolicy.REPLAY_SAFE
                ),
                (SendNamedKey("safe-effect", "Escape"),),
                baseline,
                NOW,
                ActionExpectation(baseline),
            ),
        )
        unsafe = replace(
            operation,
            envelope=replace(
                operation.envelope,
                operation_id="a-unsafe-recovery",
                action_history=("unsafe-action",),
            ),
            action_id="unsafe-action",
        )
        persist_operation(
            connection,
            unsafe.envelope,
            harness_id="codex",
            session_id="agent-1",
            request=unsafe.request,
            operation_state=unsafe,
        )
        unsafe_action = SendInterrupt(
            "unsafe-action",
            unsafe.envelope.operation_id,
            DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        )
        persist_action_record(
            connection,
            ActionRecord(
                unsafe_action.action_id,
                unsafe_action.operation_id,
                unsafe_action,
                (SendNamedKey("unsafe-effect", "C-c"),),
                baseline,
                NOW,
                ActionExpectation(baseline),
            ),
        )
        record_effect_emissions(
            connection,
            action_id=unsafe_action.action_id,
            results=(EffectEmission("unsafe-effect", EmissionStatus.EMITTED),),
            emitted_at=NOW,
        )
        connection.commit()

        frames = iter(("active", "stopped"))
        latest = {"text": ""}

        async def dimensions(_session):
            return 120, 40

        async def capture(_session, *, lines, escapes):
            del lines, escapes
            latest["text"] = next(frames)
            return latest["text"]

        async def viewport(_session, *, escapes):
            del escapes
            return latest["text"]

        monkeypatch.setattr(tmux, "pane_dimensions", dimensions)
        monkeypatch.setattr(tmux, "capture_pane", capture)
        monkeypatch.setattr(tmux, "capture_viewport", viewport)

        class Adapter:
            parser_version = "restart-test/v1"

            def __init__(self) -> None:
                self.raw = ""
                self.histories: list[tuple[str, ...]] = []
                self.priors = []
                self.lowered = []

            def parse_evidence(self, current, history):
                self.raw = current.raw_text
                self.histories.append(tuple(str(item.evidence_id) for item in history))
                return ()

            def project_observations(self, _evidence, prior):
                self.priors.append(prior)
                revision = prior.revision
                state = GenerationState(
                    GenerationPhase.RUNNING_TOOL
                    if self.raw == "active"
                    else GenerationPhase.STOPPED,
                    self.raw == "active",
                    False,
                    None,
                    None,
                    None,
                )
                return ObservationDelta(
                    {
                        "generation": Observed.present(
                            state, evidence=(), observed_at=NOW, revision=revision
                        )
                    }
                )

            def lower(self, action, snapshot):
                self.lowered.append((action, snapshot))
                return (SendNamedKey("must-not-emit", "Escape"),)

        adapter = Adapter()
        session = VerifiedHarnessControlSession.from_tmux(
            harness_kind="codex",
            terminal_session="codex-pane",
            connection=connection,
            persistence_session_id="agent-1",
            observation_adapter=adapter,
            action_adapter=adapter,
        )

        assert session.controller.snapshot == persisted
        recovered = await session.recover_pending_operations()

        assert recovered == ("a-unsafe-recovery", "z-safe-recovery")
        assert adapter.histories[0] == ("old-evidence",)
        assert adapter.priors[0].generation.knowledge is Knowledge.CONTRADICTED
        revisions = [item.revision for item in adapter.priors] + [
            session.controller.snapshot.revision
        ]
        assert revisions == sorted(revisions) and len(set(revisions)) == len(revisions)
        assert (
            session.controller.snapshot.revision.capture_sequence == HYDRATED_CAPTURE_SEQUENCE
        )
        assert adapter.lowered == []
        unsafe_row = get_operation(connection, "a-unsafe-recovery")
        assert unsafe_row is not None and unsafe_row.status == "ESCALATED"
        recovered_row = get_operation(connection, "z-safe-recovery")
        assert recovered_row is not None and recovered_row.status == "SUCCEEDED"

    asyncio.run(scenario())
