"""Live trace tests for controller-owned verified model selection."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from murder.llm.harness_control.capabilities.model_selection import (
    ModelSelectionOutcome,
    ModelSelectionPhase,
    ModelTarget,
    SelectModelOperation,
    SelectModelRequest,
)
from murder.llm.harness_control.model.actions import SendNamedKey
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceId,
    FrameId,
    HarnessId,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    Knowledge,
    ModelConfigurationState,
    ModelState,
    ObservationDelta,
    ObservationRevision,
    Observed,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import OperationEnvelope, OperationStatus
from murder.llm.harness_control.runtime.actuator import HarnessActuator
from murder.llm.harness_control.runtime.controller import HarnessController
from murder.llm.harness_control.runtime.model_driver import (
    DEFAULT_MODEL_SELECTION_DEADLINE,
    ModelDriverPolicy,
    VerifiedModelSelectionDriver,
)
from murder.llm.harness_control.runtime.observer import ObservationStore
from murder.llm.harness_control.runtime.sqlite_journal import SqliteHarnessControlJournal
from murder.state.persistence.schema import init_db

NOW = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
TARGET = ModelTarget("gpt-5.5", provider="openai", effort="high")
MODEL_ACTION_COUNT = 2
MINIMUM_DECISION_COUNT = 6


async def _no_sleep(_: float) -> None:
    return None


class StaticFrameObserver:
    def __init__(self, *states: str) -> None:
        self._states = list(states)
        self._sequence = 0

    async def capture_frame(self) -> TerminalFrame:
        self._sequence += 1
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return TerminalFrame(
            FrameId(f"frame-{self._sequence}"),
            HarnessId("test-model"),
            NOW + timedelta(seconds=self._sequence),
            120,
            40,
            state,
            False,
            0,
            self._sequence,
        )


class ModelEvidenceAdapter:
    parser_version = "model-driver-test/v1"

    def parse_evidence(self, frame, history):
        del history
        return (
            EvidenceEnvelope(
                EvidenceId(f"evidence-{frame.capture_sequence}"),
                frame.frame_id,
                frame.harness_id,
                self.parser_version,
                frame.captured_at,
                "model-test-surface",
                {"state": frame.raw_text},
                diagnostics=EvidenceDiagnostics(self.parser_version),
            ),
        )

    def project_observations(self, evidence, prior):
        del prior
        state = evidence[-1].payload["state"]
        revision = ObservationRevision(0, 0, 0)
        observed_at = NOW
        configuration = _configuration("gpt-5.5" if state != "initial" else "other-model")
        active = (
            Observed.present(
                ModelState("gpt-5.5", "high", "GPT-5.5", "openai"),
                evidence=(),
                observed_at=observed_at,
                revision=revision,
            )
            if state == "active"
            else (
                Observed.without_value(
                    Knowledge.UNKNOWN,
                    evidence=(),
                    observed_at=observed_at,
                    revision=revision,
                    explanation="active status has not repainted yet",
                )
                if state == "pending-readback"
                else Observed.present(
                    ModelState("other-model", "medium", "Other", "openai"),
                    evidence=(),
                    observed_at=observed_at,
                    revision=revision,
                )
            )
        )
        return ObservationDelta(
            {"model_configuration": _observed(configuration), "active_model": active}
        )

    def lower(self, action, snapshot):
        del snapshot
        return (SendNamedKey(f"effect-{action.action_id}", "Enter"),)


class DatabaseAwareTransport:
    def __init__(self, connection: sqlite3.Connection, events: list[str]) -> None:
        self._connection = connection
        self._events = events

    async def send_literal_keys(self, text, *, inter_key_delay) -> None:
        del text, inter_key_delay
        raise AssertionError("model lowering must use the named test effect")

    async def paste_buffer(self, text) -> None:
        del text
        raise AssertionError("model lowering must use the named test effect")

    async def send_named_key(self, key) -> None:
        assert self._connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[
            0
        ]
        self._events.append(f"effect:{key}")


def _observed(value):
    return Observed.present(
        value,
        evidence=(),
        observed_at=NOW,
        revision=ObservationRevision(0, 0, 0),
    )


def _configuration(model_id: str) -> ModelConfigurationState:
    return ModelConfigurationState(
        available=(ChoiceState("gpt-5.5", "GPT-5.5"),),
        highlighted_model_id=model_id,
        selected_model_id=model_id,
        configured_model_id=model_id,
        pending_changes=False,
        parameters=(("effort", "high"),),
    )


def _driver(*states: str):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    events: list[str] = []
    controller = HarnessController(
        ModelEvidenceAdapter(),
        ModelEvidenceAdapter(),
        ObservationStore(unknown_snapshot(HarnessId("test-model"), captured_at=NOW)),
        HarnessActuator(DatabaseAwareTransport(connection, events)),
        SqliteHarnessControlJournal(connection, session_id="model-driver-test"),
    )
    return (
        VerifiedModelSelectionDriver(
            controller,
            StaticFrameObserver(*states),
            policy=ModelDriverPolicy(observation_interval=timedelta(), maximum_observations=12),
            sleep=_no_sleep,
            now=lambda: NOW,
        ),
        controller,
        connection,
        events,
    )


def test_driver_configures_reopens_and_waits_for_delayed_active_readback() -> None:
    async def scenario() -> None:
        driver, _controller, connection, events = _driver(
            "initial",
            "initial",
            "configured",
            "configured",
            "pending-readback",
            "active",
        )

        result = await driver.select(TARGET)

        assert result.outcome is ModelSelectionOutcome.ACTIVATED
        assert result.active_model == ModelState("gpt-5.5", "high", "GPT-5.5", "openai")
        assert len(events) == MODEL_ACTION_COUNT
        actions = connection.execute(
            "SELECT semantic_action_type, emission_status FROM harness_control_actions "
            "ORDER BY requested_at"
        ).fetchall()
        assert len(actions) == MODEL_ACTION_COUNT
        assert all(row["emission_status"] == "EMITTED" for row in actions)
        assert all(row["semantic_action_type"].endswith("SelectModel") for row in actions)
        assert (
            connection.execute("SELECT COUNT(*) FROM harness_control_decisions").fetchone()[0]
            >= MINIMUM_DECISION_COUNT
        )

    asyncio.run(scenario())


def test_create_operation_defaults_to_one_minute_deadline() -> None:
    driver, _controller, _connection, _events = _driver("initial")

    operation = driver.create_operation(TARGET)

    assert operation.request.deadline == DEFAULT_MODEL_SELECTION_DEADLINE
    assert operation.envelope.deadline == NOW + DEFAULT_MODEL_SELECTION_DEADLINE


def test_resume_after_activation_effect_uses_fresh_readback_and_never_replays() -> None:
    async def scenario() -> None:
        driver, _controller, connection, events = _driver("configured")
        operation = SelectModelOperation(
            envelope=OperationEnvelope(
                operation_id="recovered-model-op",
                capability="select_model",
                status=OperationStatus.RUNNING,
                phase=ModelSelectionPhase.AWAITING_ACTIVE_READBACK,
                created_at=NOW,
                updated_at=NOW,
                deadline=NOW + timedelta(minutes=1),
            ),
            request=SelectModelRequest(TARGET, timedelta(minutes=1)),
            configuration_action_id="configured-before-restart",
            activation_action_id="activated-before-restart",
            configuration_baseline_revision=ObservationRevision(0, 0, 0),
            activation_baseline_revision=ObservationRevision(0, 0, 0),
        )

        result = await driver.resume(operation)

        assert result.outcome is ModelSelectionOutcome.ESCALATED
        assert events == []
        assert connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0] == 0
        persisted = connection.execute(
            "SELECT status FROM harness_control_operations WHERE operation_id = ?",
            ("recovered-model-op",),
        ).fetchone()
        assert persisted["status"] == "ESCALATED"
        decision = connection.execute(
            "SELECT selected_decision FROM harness_control_decisions "
            "WHERE operation_id = ? ORDER BY id DESC LIMIT 1",
            ("recovered-model-op",),
        ).fetchone()
        assert decision["selected_decision"] == "ESCALATE"

    asyncio.run(scenario())
