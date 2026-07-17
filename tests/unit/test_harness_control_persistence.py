"""Durable evidence and verified-action persistence contracts."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from murder.llm.harness_control.model.actions import (
    CommitPromptSubmission,
    DuplicatePolicy,
    EffectEmission,
    EmissionStatus,
    InputChunk,
    InputProvenance,
    SendNamedKey,
)
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceRef,
    ScreenRegionRef,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    AuthoritativeFacts,
    ComposerActionability,
    ComposerState,
    Knowledge,
    ObservationHealth,
    ObservationRevision,
    ObservationSnapshot,
    Observed,
)
from murder.llm.harness_control.model.operations import (
    ActionExpectation,
    ActionRecord,
    ControllerDecisionKind,
    DecisionRecord,
    OperationEnvelope,
    OperationStatus,
    PromptPayload,
    SubmitPhase,
    SubmitPromptOperation,
    SubmitPromptRequest,
)
from murder.llm.harness_control.runtime.sqlite_journal import SqliteHarnessControlJournal
from murder.state.persistence import harness_control as harness_control_persistence
from murder.state.persistence.harness_control import (
    get_evidence,
    latest_observation,
    list_evidence,
    load_recovery_candidates,
    persist_action_record,
    persist_evidence,
    persist_frame,
    persist_observation_snapshot,
    persist_operation,
    record_effect_emissions,
)
from murder.state.persistence.schema import get_db, init_db

NOW = datetime(2026, 7, 11, 12, 30, tzinfo=timezone.utc)
REVISION = ObservationRevision(3, 41, 7)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    db = get_db(tmp_path / "test.db")
    init_db(db)
    return db


def _frame() -> TerminalFrame:
    return TerminalFrame(
        frame_id="frame-41",
        harness_id="codex",
        captured_at=NOW,
        width=220,
        height=50,
        raw_text="\x1b[31mvisible frame\x1b[0m",
        ansi_preserved=True,
        pane_epoch=3,
        capture_sequence=41,
    )


def _ref() -> EvidenceRef:
    return EvidenceRef(
        evidence_id="evidence-composer",
        frame_id="frame-41",
        source_regions=(ScreenRegionRef("composer", start_line=45, end_line=46),),
    )


def _unknown(*, ref: EvidenceRef) -> Observed[object]:
    return Observed.without_value(
        Knowledge.UNKNOWN,
        evidence=(ref,),
        observed_at=NOW,
        revision=REVISION,
        explanation="not visible in this fixture",
    )


def _snapshot() -> ObservationSnapshot:
    ref = _ref()
    composer = Observed.present(
        ComposerState(
            text="draft",
            normalized_text="draft",
            content_fingerprint="fprint",
            cursor_visible=True,
            focused=True,
            actionability=ComposerActionability.ACTIONABLE,
            is_partial=False,
            accepts_submission=True,
        ),
        evidence=(ref,),
        observed_at=NOW,
        revision=REVISION,
    )
    return ObservationSnapshot(
        revision=REVISION,
        harness_id="codex",
        captured_at=NOW,
        surface=_unknown(ref=ref),
        composer=composer,
        generation=_unknown(ref=ref),
        transcript_tail=_unknown(ref=ref),
        modal=_unknown(ref=ref),
        question=_unknown(ref=ref),
        permission_request=_unknown(ref=ref),
        active_model=_unknown(ref=ref),
        model_configuration=_unknown(ref=ref),
        settings=_unknown(ref=ref),
        info=_unknown(ref=ref),
        usage=_unknown(ref=ref),
        tool_activity=_unknown(ref=ref),
        health=ObservationHealth(parser_status="healthy"),
        facts=AuthoritativeFacts(),
    )


def _operation() -> OperationEnvelope[SubmitPhase]:
    return OperationEnvelope(
        operation_id="submit-1",
        capability="submit_prompt",
        status=OperationStatus.RUNNING,
        phase=SubmitPhase.AWAITING_ACKNOWLEDGMENT,
        created_at=NOW,
        updated_at=NOW,
        deadline=None,
        last_observation_revision=REVISION,
    )


def _operation_state() -> SubmitPromptOperation:
    return SubmitPromptOperation(
        envelope=_operation(),
        request=SubmitPromptRequest(
            payload=PromptPayload(
                chunks=(InputChunk("draft", InputProvenance.USER_TYPED, "chunk-1"),),
                normalized_text="draft",
                fingerprint="fprint",
            ),
            await_completion=False,
            submission_deadline=timedelta(seconds=30),
        ),
        payload_fingerprint="fprint",
    )


def _decision() -> DecisionRecord:
    return DecisionRecord(
        operation_id="submit-1",
        observation_revision=REVISION,
        phase_before=SubmitPhase.READY_TO_COMMIT.name,
        predicate_results=(),
        selected_decision=ControllerDecisionKind.EMIT_ACTION,
        selected_action_id="commit-1",
        reason="test verified transition",
        decided_at=NOW,
    )


def _action_record(*, effects: tuple[SendNamedKey, ...] | None = None) -> ActionRecord:
    action = CommitPromptSubmission(
        action_id="commit-1",
        operation_id="submit-1",
        duplicate_policy=DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
    )
    return ActionRecord(
        action_id="commit-1",
        operation_id="submit-1",
        semantic_action=action,
        lowered_effects=effects or (SendNamedKey(effect_id="effect-enter", key="Enter"),),
        selected_from_revision=REVISION,
        requested_at=NOW,
        expectation=ActionExpectation(require_revision_after=REVISION),
    )


def _transition_row_counts(conn: sqlite3.Connection) -> tuple[int, int, int, int]:
    return tuple(
        int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            "harness_control_operations",
            "harness_control_decisions",
            "harness_control_actions",
            "harness_control_effects",
        )
    )


def test_evidence_retains_unpromoted_harness_specific_payload_and_provenance(conn) -> None:
    frame = _frame()
    persist_frame(conn, frame, session_id="session-a")
    evidence = EvidenceEnvelope(
        evidence_id="evidence-composer",
        frame_id=frame.frame_id,
        harness_id="codex",
        parser_version="codex-evidence/v3",
        captured_at=NOW,
        evidence_type="codex.modal",
        payload={
            "question_picker": {"tabs": ["Settings", "Usage"], "filter": "opus"},
            "background_terminals": [{"name": "mcp", "state": "starting"}],
        },
        source_regions=(ScreenRegionRef("modal", start_line=2, end_line=22),),
        diagnostics=EvidenceDiagnostics(
            parser_name="codex",
            messages=("unpromoted model parameter retained",),
            unrecognized_regions=(ScreenRegionRef("footer", start_line=49),),
        ),
    )
    persist_evidence(conn, evidence)

    recovered = get_evidence(conn, "evidence-composer")
    assert recovered is not None
    assert recovered.parser_version == "codex-evidence/v3"
    assert recovered.payload["question_picker"]["tabs"] == ["Settings", "Usage"]
    assert recovered.payload["background_terminals"][0]["state"] == "starting"
    assert recovered.source_regions[0].label == "modal"
    assert recovered.diagnostics.unrecognized_regions[0].label == "footer"
    assert list_evidence(conn, harness_id="codex", evidence_type="codex.modal") == [recovered]


def test_evidence_requires_its_raw_frame_and_matching_harness(conn) -> None:
    evidence = EvidenceEnvelope(
        evidence_id="evidence-orphan",
        frame_id="missing-frame",
        harness_id="codex",
        parser_version="v1",
        captured_at=NOW,
        evidence_type="codex.composer",
        payload={},
    )
    with pytest.raises(ValueError, match="unknown frame"):
        persist_evidence(conn, evidence)

    persist_frame(conn, _frame())
    mismatched = EvidenceEnvelope(
        evidence_id="evidence-wrong-harness",
        frame_id="frame-41",
        harness_id="pi",
        parser_version="v1",
        captured_at=NOW,
        evidence_type="pi.footer",
        payload={},
    )
    with pytest.raises(ValueError, match="does not match"):
        persist_evidence(conn, mismatched)


def test_action_and_effects_are_durable_before_emission_result(conn) -> None:
    persist_operation(conn, _operation(), harness_id="codex", session_id="session-a")
    action = CommitPromptSubmission(
        action_id="commit-1",
        operation_id="submit-1",
        duplicate_policy=DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
    )
    record = ActionRecord(
        action_id="commit-1",
        operation_id="submit-1",
        semantic_action=action,
        lowered_effects=(SendNamedKey(effect_id="effect-enter", key="Enter"),),
        selected_from_revision=REVISION,
        requested_at=NOW,
        expectation=ActionExpectation(require_revision_after=REVISION),
    )
    persist_action_record(conn, record)

    before = conn.execute(
        "SELECT emission_status FROM harness_control_effects WHERE effect_id = 'effect-enter'"
    ).fetchone()
    assert before["emission_status"] == "PENDING"

    record_effect_emissions(
        conn,
        action_id="commit-1",
        results=(EffectEmission("effect-enter", EmissionStatus.EMITTED),),
        emitted_at=NOW,
    )
    after = conn.execute(
        "SELECT emission_status FROM harness_control_effects WHERE effect_id = 'effect-enter'"
    ).fetchone()
    action_after = conn.execute(
        "SELECT emission_status FROM harness_control_actions WHERE action_id = 'commit-1'"
    ).fetchone()
    assert after["emission_status"] == "EMITTED"
    assert action_after["emission_status"] == "EMITTED"


@pytest.mark.parametrize("stage", ("operation", "decision", "action"))
def test_journal_rolls_back_the_entire_transition_when_a_stage_fails(
    conn, monkeypatch, stage
) -> None:
    journal = SqliteHarnessControlJournal(conn, session_id="session-a")
    target_name = {
        "operation": "persist_operation",
        "decision": "persist_decision_record",
        "action": "persist_action_record",
    }[stage]
    original = getattr(harness_control_persistence, target_name)

    def fail_at_stage(*args, **kwargs):
        if stage != "operation":
            original(*args, **kwargs)
        raise RuntimeError(f"injected {stage} persistence failure")

    monkeypatch.setattr(harness_control_persistence, target_name, fail_at_stage)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match=f"injected {stage}"):
            await journal.record_transition(
                _operation_state(), _snapshot(), _decision(), _action_record()
            )

    asyncio.run(scenario())
    assert _transition_row_counts(conn) == (0, 0, 0, 0)


def test_journal_rolls_back_a_transition_when_a_later_effect_insert_fails(conn) -> None:
    conn.execute(
        """
        CREATE TRIGGER fail_second_effect
        BEFORE INSERT ON harness_control_effects
        WHEN NEW.effect_id = 'effect-two'
        BEGIN
            SELECT RAISE(FAIL, 'injected later effect failure');
        END
        """
    )
    journal = SqliteHarnessControlJournal(conn, session_id="session-a")
    record = _action_record(
        effects=(
            SendNamedKey(effect_id="effect-one", key="Down"),
            SendNamedKey(effect_id="effect-two", key="Enter"),
        )
    )

    async def scenario() -> None:
        with pytest.raises(sqlite3.IntegrityError, match="injected later effect failure"):
            await journal.record_transition(_operation_state(), _snapshot(), _decision(), record)

    asyncio.run(scenario())
    assert _transition_row_counts(conn) == (0, 0, 0, 0)


def test_journal_prepare_action_rolls_back_all_effects_when_a_later_insert_fails(conn) -> None:
    persist_operation(conn, _operation(), harness_id="codex", session_id="session-a")
    conn.execute(
        """
        CREATE TRIGGER fail_manual_second_effect
        BEFORE INSERT ON harness_control_effects
        WHEN NEW.effect_id = 'effect-two'
        BEGIN
            SELECT RAISE(FAIL, 'injected later effect failure');
        END
        """
    )
    journal = SqliteHarnessControlJournal(conn, session_id="session-a")
    record = _action_record(
        effects=(
            SendNamedKey(effect_id="effect-one", key="Down"),
            SendNamedKey(effect_id="effect-two", key="Enter"),
        )
    )

    async def scenario() -> None:
        with pytest.raises(sqlite3.IntegrityError, match="injected later effect failure"):
            await journal.prepare_action(record)

    asyncio.run(scenario())
    assert _transition_row_counts(conn) == (1, 0, 0, 0)


def test_recovery_loads_unfinished_operation_with_fresh_snapshot_and_unsafe_marker(conn) -> None:
    frame = _frame()
    persist_frame(conn, frame, session_id="session-a")
    persist_operation(conn, _operation(), harness_id="codex", session_id="session-a")
    persist_observation_snapshot(conn, _snapshot(), session_id="session-a")

    action = CommitPromptSubmission(
        action_id="commit-1",
        operation_id="submit-1",
        duplicate_policy=DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
    )
    persist_action_record(
        conn,
        ActionRecord(
            action_id="commit-1",
            operation_id="submit-1",
            semantic_action=action,
            lowered_effects=(SendNamedKey(effect_id="effect-enter", key="Enter"),),
            selected_from_revision=REVISION,
            requested_at=NOW,
            expectation=ActionExpectation(require_revision_after=REVISION),
        ),
    )
    record_effect_emissions(
        conn,
        action_id="commit-1",
        results=(EffectEmission("effect-enter", EmissionStatus.EMITTED),),
        emitted_at=NOW,
    )

    latest = latest_observation(conn, harness_id="codex", session_id="session-a")
    candidates = load_recovery_candidates(conn, harness_id="codex", session_id="session-a")
    assert latest is not None and latest.revision == REVISION
    assert len(candidates) == 1
    assert candidates[0].latest_observation == latest
    assert candidates[0].has_ambiguous_unsafe_effect is True
    assert candidates[0].operation.phase_type.endswith("SubmitPhase")
