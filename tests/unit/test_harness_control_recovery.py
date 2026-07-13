import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from murder.llm.harness_control.capabilities.model_selection import (
    ModelSelectionPhase,
    ModelTarget,
    SelectModelOperation,
    SelectModelRequest,
)
from murder.llm.harness_control.capabilities.permissions import (
    AnswerPermissionOperation,
    AnswerPermissionPhase,
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
)
from murder.llm.harness_control.capabilities.questions import (
    AnswerQuestionOperation,
    AnswerQuestionPhase,
    QuestionAnswerRequest,
)
from murder.llm.harness_control.capabilities.restoration import (
    InterruptOperation,
    InterruptPhase,
    InterruptRequest,
    RestorationPhase,
    RestoreComposerOperation,
    RestoreComposerRequest,
)
from murder.llm.harness_control.capabilities.usage import UsageOperation, UsagePhase, UsageRequest
from murder.llm.harness_control.model.actions import (
    InputChunk,
    InputProvenance,
    QuestionAnswerMode,
    QuestionChoiceSelection,
)
from murder.llm.harness_control.model.observations import ObservationRevision
from murder.llm.harness_control.model.operations import (
    OperationEnvelope,
    OperationStatus,
    PromptPayload,
    SubmitPhase,
    SubmitPromptOperation,
    SubmitPromptRequest,
)
from murder.llm.harness_control.runtime.recovery import (
    RecoveryDecodeError,
    RecoveryDisposition,
    classify_recovery_candidate,
    reconstruct_persisted_operation,
)
from murder.state.persistence.harness_control import (
    PersistedAction,
    PersistedOperation,
    RecoveryCandidate,
    get_operation,
    persist_operation,
)
from murder.state.persistence.schema import get_db, init_db

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
REVISION = ObservationRevision(2, 9, 4)


@pytest.fixture()
def conn(tmp_path) -> sqlite3.Connection:
    connection = get_db(tmp_path / "recovery.db")
    init_db(connection)
    return connection


def _envelope(capability: str, phase: object) -> OperationEnvelope[object]:
    return OperationEnvelope(
        "op",
        capability,
        OperationStatus.RUNNING,
        phase,
        NOW,
        NOW,
        NOW + timedelta(minutes=2),
        3,
        REVISION,
        ("action-1",),
        (),
    )


def _operations() -> tuple[object, ...]:
    prompt = PromptPayload(
        (InputChunk("hello", InputProvenance.USER_TYPED, "chunk-1"),), "hello", "fp"
    )
    return (
        SubmitPromptOperation(
            _envelope("submit_prompt", SubmitPhase.AWAITING_ACKNOWLEDGMENT),
            SubmitPromptRequest(prompt, True, timedelta(seconds=20), timedelta(minutes=1)),
            "fp",
            commit_action_id="action-1",
            baseline_revision=REVISION,
            commit_emitted_at=NOW,
        ),
        SelectModelOperation(
            _envelope("select_model", ModelSelectionPhase.AWAITING_ACTIVE_READBACK),
            SelectModelRequest(
                ModelTarget("gpt", "openai", "high", fast_enabled=True), timedelta(minutes=1)
            ),
            activation_action_id="action-1",
            activation_baseline_revision=REVISION,
        ),
        AnswerQuestionOperation(
            _envelope("answer_question", AnswerQuestionPhase.AWAITING_ACKNOWLEDGMENT),
            QuestionAnswerRequest(
                "q-1",
                None,
                QuestionAnswerMode.MULTIPLE,
                (
                    QuestionChoiceSelection("a", "Alpha"),
                    QuestionChoiceSelection("b", "Beta"),
                ),
            ),
            "action-1",
            REVISION,
        ),
        AnswerPermissionOperation(
            _envelope("answer_permission", AnswerPermissionPhase.AWAITING_ACKNOWLEDGMENT),
            PermissionAnswerRequest(
                "permission-1",
                None,
                PermissionResponseTarget(
                    "once", "Allow once", PermissionDecisionKind.ALLOW_ONCE
                ),
                frozenset({"network", "write"}),
            ),
            "action-1",
            REVISION,
        ),
        RestoreComposerOperation(
            _envelope("restore_composer", RestorationPhase.AWAITING_SURFACE),
            RestoreComposerRequest(timedelta(seconds=10)),
            REVISION,
            "action-1",
        ),
        InterruptOperation(
            _envelope("interrupt", InterruptPhase.AWAITING_ACKNOWLEDGMENT),
            InterruptRequest(timedelta(seconds=5)),
            REVISION,
            "action-1",
        ),
        UsageOperation(
            _envelope("usage", UsagePhase.RESTORING_SURFACE),
            UsageRequest(timedelta(seconds=30), True, "status"),
            REVISION,
            "action-1",
            "restore-1",
            REVISION,
        ),
    )


def _candidate(action: PersistedAction) -> RecoveryCandidate:
    operation = PersistedOperation(
        "op",
        "codex",
        "session",
        "submit_prompt",
        "RUNNING",
        "COMMIT_EMITTED",
        {},
        {},
        {},
        NOW,
        NOW,
        None,
        1,
        None,
        (),
    )
    return RecoveryCandidate(operation, None, (action,))


@pytest.mark.parametrize("effect_status", ["PENDING", "EMITTED", "FAILED"])
def test_restart_after_emitted_unsafe_commit_is_ambiguous(effect_status: str) -> None:
    plan = classify_recovery_candidate(
        _candidate(
            PersistedAction(
                "commit", "op", "AMBIGUOUS_AFTER_EMISSION", effect_status, (effect_status,)
            )
        )
    )
    assert plan.disposition is RecoveryDisposition.AMBIGUOUS_UNSAFE_EFFECT
    assert "fresh evidence" in plan.reason


def test_safe_precondition_action_still_requires_fresh_observation() -> None:
    plan = classify_recovery_candidate(
        _candidate(
            PersistedAction(
                "clear", "op", "REPLAY_SAFE_WHILE_PRECONDITION_HOLDS", "EMITTED", ("EMITTED",)
            )
        )
    )
    assert plan.disposition is RecoveryDisposition.REQUIRE_FRESH_OBSERVATION


@pytest.mark.parametrize("operation", _operations(), ids=lambda op: type(op).__name__)
def test_persisted_semantic_operations_reconstruct_exact_typed_state(
    conn: sqlite3.Connection, operation: object
) -> None:
    persist_operation(
        conn,
        operation.envelope,
        harness_id="codex",
        session_id="session",
        request=operation.request,
        operation_state=operation,
    )
    persisted = get_operation(conn, "op")
    assert persisted is not None

    assert reconstruct_persisted_operation(persisted) == operation


def test_reconstruction_rejects_unknown_or_schema_incompatible_types() -> None:
    operation = _candidate(PersistedAction("safe", "op", "REPLAY_SAFE", "PENDING", ())).operation
    unknown = replace(operation, operation_state={"$type": "plugin.UnknownOperation", "fields": {}})
    incompatible = replace(
        operation,
        operation_state={
            "$type": "murder.llm.harness_control.model.operations.SubmitPromptOperation",
            "fields": {"envelope": {"$type": "tuple", "items": []}},
        },
    )

    with pytest.raises(RecoveryDecodeError, match="unsupported persisted type"):
        reconstruct_persisted_operation(unknown)
    with pytest.raises(RecoveryDecodeError, match="schema mismatch"):
        reconstruct_persisted_operation(incompatible)
