from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from murder.llm.harness_control.model import (
    DuplicatePolicy,
    HarnessId,
    ObservationDelta,
    SemanticAction,
    SendNamedKey,
    TerminalFrame,
    unknown_snapshot,
)
from murder.llm.harness_control.model.evidence import FrameId
from murder.llm.harness_control.model.operations import ControllerDecision, ControllerDecisionKind
from murder.llm.harness_control.runtime import HarnessActuator, HarnessController
from murder.llm.harness_control.runtime.observer import ObservationStore


class RecordingTransport:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def send_literal_keys(self, text: str, *, inter_key_delay: object | None) -> None:
        self.events.append(f"literal:{text}")

    async def paste_buffer(self, text: str) -> None:
        self.events.append(f"paste:{text}")

    async def send_named_key(self, key: str) -> None:
        self.events.append(f"key:{key}")


class Adapter:
    parser_version = "test-v1"

    def parse_evidence(self, frame, history):
        return ()

    def project_observations(self, evidence, prior):
        return ObservationDelta({})

    def lower(self, action, snapshot):
        return (SendNamedKey("effect-1", "Enter"),)


class Journal:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def record_frame(self, frame) -> None:
        self.events.append("frame")

    async def record_evidence(self, evidence) -> None:
        self.events.append("evidence")

    async def record_snapshot(self, snapshot) -> None:
        self.events.append("snapshot")

    async def record_delta(self, snapshot, delta) -> None:
        self.events.append("delta")

    async def record_operation(self, operation, snapshot) -> None:
        self.events.append("operation")

    async def record_decision(self, decision) -> None:
        self.events.append("decision")

    async def prepare_action(self, action) -> None:
        self.events.append("prepared")

    async def record_transition(self, operation, snapshot, decision, action) -> None:
        self.events.extend(("operation", "decision"))
        if action is not None:
            self.events.append("prepared")

    async def record_emission(self, action, result) -> None:
        self.events.append("emission")


class Operation:
    class Envelope:
        operation_id = "operation-1"

    envelope = Envelope()


def test_controller_persists_action_before_terminal_effect() -> None:
    async def scenario() -> None:
        events: list[str] = []
        now = datetime.now(timezone.utc)
        controller = HarnessController(
            Adapter(),
            Adapter(),
            ObservationStore(unknown_snapshot(HarnessId("codex"), captured_at=now)),
            HarnessActuator(RecordingTransport(events)),
            Journal(events),
        )
        await controller.ingest_frame(
            TerminalFrame(FrameId("frame-1"), HarnessId("codex"), now, 120, 40, "", False, 0, 1)
        )
        action = SemanticAction("action-1", "operation-1", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION)

        def reconcile(operation, snapshot, at):
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION, None, action, "exercise persistence ordering"
            )

        def advance(operation, decision, snapshot, at):
            events.append("advanced")
            return operation

        await controller.reconcile_once(
            Operation(), reconcile, phase_name="READY_TO_COMMIT", advance=advance
        )
        assert events[:8] == [
            "frame",
            "evidence",
            "snapshot",
            "delta",
            "operation",
            "advanced",
            "operation",
            "decision",
        ]
        assert events.index("prepared") < events.index("key:Enter") < events.index("emission")

    asyncio.run(scenario())


def test_action_lowering_uses_the_exact_snapshot_that_selected_the_decision() -> None:
    async def scenario() -> None:
        events: list[str] = []
        now = datetime.now(timezone.utc)
        frame_one = TerminalFrame(
            FrameId("frame-1"), HarnessId("codex"), now, 120, 40, "one", False, 0, 1
        )
        frame_two = TerminalFrame(
            FrameId("frame-2"), HarnessId("codex"), now, 120, 40, "two", False, 0, 2
        )

        class RecordingAdapter(Adapter):
            def __init__(self) -> None:
                self.lowering_snapshots = []

            def lower(self, action, snapshot):
                self.lowering_snapshots.append(snapshot)
                return super().lower(action, snapshot)

        class InterleavingJournal(Journal):
            def __init__(self) -> None:
                super().__init__(events)
                self.controller = None
                self.prepared = []

            async def record_transition(self, operation, snapshot, decision, action) -> None:
                await super().record_transition(operation, snapshot, decision, action)
                if action is not None:
                    self.prepared.append(action)
                await self.controller.ingest_frame(frame_two)

        adapter = RecordingAdapter()
        journal = InterleavingJournal()
        controller = HarnessController(
            adapter,
            adapter,
            ObservationStore(unknown_snapshot(HarnessId("codex"), captured_at=now)),
            HarnessActuator(RecordingTransport(events)),
            journal,
        )
        journal.controller = controller
        selected_snapshot = await controller.ingest_frame(frame_one)
        action = SemanticAction("action-1", "operation-1", DuplicatePolicy.REPLAY_SAFE)

        def reconcile(operation, snapshot, at):
            assert snapshot is selected_snapshot
            return ControllerDecision(
                ControllerDecisionKind.EMIT_ACTION, None, action, "selected from frame one"
            )

        await controller.reconcile_once(
            Operation(), reconcile, phase_name="READY", advance=lambda op, *_args: op
        )

        assert (
            controller.snapshot.revision.capture_sequence == frame_two.capture_sequence
        )
        assert adapter.lowering_snapshots == [selected_snapshot]
        assert journal.prepared[0].selected_from_revision == selected_snapshot.revision

        stale = TerminalFrame(
            FrameId("stale-frame"), HarnessId("codex"), now, 120, 40, "stale", False, 0, 1
        )
        assert await controller.ingest_frame(stale) is controller.snapshot
        assert controller.snapshot.revision.capture_sequence == frame_two.capture_sequence

    asyncio.run(scenario())
