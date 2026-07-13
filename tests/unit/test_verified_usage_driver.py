"""Trace tests for controller-owned verified usage collection."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from murder.llm.harness_control.adapters.antigravity import AntigravityHarnessAdapter
from murder.llm.harness_control.adapters.claude_code import ClaudeCodeAdapter
from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.adapters.cursor import CursorHarnessAdapter
from murder.llm.harness_control.adapters.pi import PiHarnessAdapter
from murder.llm.harness_control.capabilities.usage import (
    UsageOperation,
    UsagePhase,
    UsageRequest,
)
from murder.llm.harness_control.model.actions import (
    DismissOverlay,
    DuplicatePolicy,
    RequestUsage,
    SendLiteralKeys,
    SendNamedKey,
)
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceId,
    FrameId,
    HarnessId,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    Knowledge,
    ObservationDelta,
    ObservationRevision,
    Observed,
    SurfaceKind,
    SurfaceState,
    UsageState,
    UsageWindow,
    unknown_snapshot,
)
from murder.llm.harness_control.model.operations import OperationEnvelope, OperationStatus
from murder.llm.harness_control.runtime.actuator import HarnessActuator
from murder.llm.harness_control.runtime.controller import HarnessController
from murder.llm.harness_control.runtime.observer import ObservationStore
from murder.llm.harness_control.runtime.sqlite_journal import SqliteHarnessControlJournal
from murder.llm.harness_control.runtime.usage_driver import (
    UsageCollectionOutcome,
    UsageDriverPolicy,
    VerifiedUsageDriver,
)
from murder.state.persistence.schema import init_db

NOW = datetime(2035, 7, 12, 12, tzinfo=timezone.utc)
EXPECTED_PERCENT_USED = 25.0
MINIMUM_USAGE_TRACE_FRAMES = 2


async def _no_sleep(_: float) -> None:
    return None


class _Frames:
    def __init__(self, *states: str) -> None:
        self._states = list(states)
        self._sequence = 0

    async def capture_frame(self) -> TerminalFrame:
        self._sequence += 1
        state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
        return TerminalFrame(
            FrameId(f"usage-frame-{self._sequence}"),
            HarnessId("usage-test"),
            NOW + timedelta(seconds=self._sequence),
            120,
            40,
            state,
            False,
            0,
            self._sequence,
        )


class _UsageAdapter:
    parser_version = "usage-driver-test/v1"

    def parse_evidence(self, frame, history):
        del history
        return (
            EvidenceEnvelope(
                EvidenceId(f"usage-evidence-{frame.capture_sequence}"),
                frame.frame_id,
                frame.harness_id,
                self.parser_version,
                frame.captured_at,
                "usage-test-surface",
                {"state": frame.raw_text},
                diagnostics=EvidenceDiagnostics(self.parser_version),
            ),
        )

    def project_observations(self, evidence, prior):
        del prior
        state = evidence[-1].payload["state"]
        revision = ObservationRevision(0, 0, 0)
        if state == "usage":
            usage = Observed.present(
                UsageState(
                    "test-model",
                    "test-plan",
                    (UsageWindow("five-hour", EXPECTED_PERCENT_USED, None, "in 2h"),),
                    "CURRENT",
                    SurfaceKind.USAGE_PANEL,
                    None,
                ),
                evidence=(),
                observed_at=NOW,
                revision=revision,
            )
            surface = _observed_surface(SurfaceKind.USAGE_PANEL, blocks=True)
        else:
            usage = Observed.without_value(
                Knowledge.UNKNOWN,
                evidence=(),
                observed_at=NOW,
                revision=revision,
                explanation="usage panel is not visible",
            )
            surface = _observed_surface(SurfaceKind.COMPOSER, blocks=False)
        return ObservationDelta({"usage": usage, "surface": surface})

    def lower(self, action, snapshot):
        del snapshot
        if isinstance(action, RequestUsage):
            return (SendNamedKey(f"{action.action_id}:usage", "UsageProbe"),)
        assert isinstance(action, DismissOverlay)
        return (SendNamedKey(f"{action.action_id}:dismiss", "Escape"),)


class _Transport:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.effects: list[str] = []

    async def send_literal_keys(self, text, *, inter_key_delay) -> None:
        del inter_key_delay
        self.effects.append(text)

    async def paste_buffer(self, text) -> None:
        self.effects.append(text)

    async def send_named_key(self, key) -> None:
        assert self.connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0]
        self.effects.append(key)


def _observed_surface(kind: SurfaceKind, *, blocks: bool):
    return Observed.present(
        SurfaceState(kind, frozenset({kind}), kind, blocks, blocks),
        evidence=(),
        observed_at=NOW,
        revision=ObservationRevision(0, 0, 0),
    )


def _driver(*states: str):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    transport = _Transport(connection)
    controller = HarnessController(
        _UsageAdapter(),
        _UsageAdapter(),
        ObservationStore(unknown_snapshot(HarnessId("usage-test"), captured_at=NOW)),
        HarnessActuator(transport),
        SqliteHarnessControlJournal(connection, session_id="usage-driver-test"),
    )
    return (
        VerifiedUsageDriver(
            controller,
            _Frames(*states),
            policy=UsageDriverPolicy(observation_interval=timedelta(), maximum_observations=8),
            sleep=_no_sleep,
            now=lambda: NOW,
        ),
        connection,
        transport,
    )


def _request() -> RequestUsage:
    return RequestUsage(
        "usage-action", "usage-op", DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS
    )


def test_verified_usage_driver_records_request_then_requires_fresh_usage_evidence() -> None:
    async def scenario() -> None:
        driver, connection, transport = _driver("composer", "usage", "composer")

        result = await driver.collect(UsageRequest(timedelta(minutes=1), require_current=True))

        assert result.outcome is UsageCollectionOutcome.COLLECTED
        assert result.usage is not None
        assert result.usage.windows[0].percent_used == EXPECTED_PERCENT_USED
        assert transport.effects == ["UsageProbe", "Escape"]
        action = connection.execute(
            "SELECT semantic_action_type, emission_status FROM harness_control_actions"
        ).fetchone()
        assert action["semantic_action_type"].endswith("RequestUsage")
        assert action["emission_status"] == "EMITTED"
        assert (
            connection.execute("SELECT COUNT(*) FROM harness_control_frames").fetchone()[0]
            >= MINIMUM_USAGE_TRACE_FRAMES
        )

    asyncio.run(scenario())


def test_usage_driver_uses_visible_current_usage_without_emitting_a_probe() -> None:
    async def scenario() -> None:
        driver, connection, transport = _driver("usage")

        result = await driver.collect_usage(require_current=True)

        assert result.outcome is UsageCollectionOutcome.COLLECTED
        assert transport.effects == []
        assert connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0] == 0

    asyncio.run(scenario())


def test_resume_after_request_emission_never_replays_usage_command() -> None:
    async def scenario() -> None:
        driver, connection, transport = _driver("composer")
        operation = UsageOperation(
            OperationEnvelope(
                "recovered-usage",
                "usage",
                OperationStatus.RUNNING,
                UsagePhase.AWAITING_FRESH_USAGE,
                NOW,
                NOW,
                NOW + timedelta(minutes=1),
            ),
            UsageRequest(timedelta(minutes=1)),
            baseline_revision=ObservationRevision(0, 0, 0),
            request_action_id="usage-request-before-restart",
        )

        result = await driver.resume(operation)

        assert result.outcome is UsageCollectionOutcome.ESCALATED
        assert transport.effects == []
        assert connection.execute("SELECT COUNT(*) FROM harness_control_actions").fetchone()[0] == 0

    asyncio.run(scenario())


def test_usage_lowering_is_harness_specific_and_side_channel_or_unsupported_cases_decline() -> None:
    snapshot = unknown_snapshot(HarnessId("test"), captured_at=NOW)

    codex = CodexHarnessAdapter().lower(_request(), snapshot)
    assert [effect.key for effect in codex if isinstance(effect, SendNamedKey)] == [
        "Escape",
        "Enter",
        "Enter",
    ]
    assert [effect.text for effect in codex if isinstance(effect, SendLiteralKeys)] == ["/status"]

    for adapter in (ClaudeCodeAdapter(), AntigravityHarnessAdapter()):
        effects = adapter.lower(_request(), snapshot)
        assert [effect.key for effect in effects if isinstance(effect, SendNamedKey)] == [
            "Escape",
            "Enter",
        ]
        assert [effect.text for effect in effects if isinstance(effect, SendLiteralKeys)] == [
            "/usage"
        ]

    with pytest.raises(ValueError, match="HTTP evidence"):
        CursorHarnessAdapter().lower(_request(), snapshot)
    with pytest.raises(ValueError, match="fixture-backed terminal usage"):
        PiHarnessAdapter().lower(_request(), snapshot)
