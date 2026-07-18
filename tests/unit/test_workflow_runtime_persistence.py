from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from murder.state.persistence.schema import init_db
from murder.state.persistence.workflow_runs import (
    SignalDeduplicationConflictError,
    StaleWorkflowRevisionError,
    apply_transition_plan,
    create_workflow_run,
    enqueue_workflow_signal,
    get_workflow_signal,
    list_workflow_signals,
    list_workflow_waits,
    load_workflow_decision_input,
    require_workflow_run,
)
from murder.work.workflows.definition import StageDef, WorkflowDef
from murder.work.workflows.runtime import (
    ActivityFinishedSignal,
    ActivityWait,
    ApprovalResolvedSignal,
    ApprovalWait,
    Correlation,
    ExternalSignalWait,
    ExternalWorkflowSignal,
    FactDraft,
    JoinWait,
    PrincipalKind,
    PrincipalRef,
    ResourceWait,
    StageRunState,
    StageStatus,
    StateReplacement,
    StaticDagWorkflowStateV1,
    TimerFiredSignal,
    TimerWait,
    VersionedState,
    WorkflowRunRecord,
    WorkflowSignalPayload,
    WorkflowSignalRecord,
    WorkflowStatus,
    WorkflowTransitionPlan,
    versioned_state,
)
from murder.work.workflows.static_dag import StaticDagWorkflowMachine

NOW = datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc)
DEFINITION_VERSION = 3
DECISION_REVISION = 7
FINITE_SIGNAL_LIMIT = 2


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def _state(status: StageStatus = StageStatus.READY) -> VersionedState:
    return versioned_state(
        StaticDagWorkflowStateV1(
            inputs={"subject": "workflow runtime"},
            stages=(StageRunState(stage_id="one", status=status),),
        ),
        schema_name="static_dag",
        schema_version=1,
    )


def _run(
    *,
    workflow_id: UUID | None = None,
    status: WorkflowStatus = WorkflowStatus.WAITING,
) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        workflow_id=workflow_id or uuid4(),
        definition_name="test-workflow",
        definition_version=DEFINITION_VERSION,
        status=status,
        revision=0,
        state=_state(),
        created_at=NOW,
        updated_at=NOW,
        started_by=PrincipalRef(kind=PrincipalKind.USER, id="luke"),
        correlation=Correlation(correlation_id=uuid4()),
    )


def test_authoritative_run_round_trips_without_python_execution_state() -> None:
    conn = _conn()
    run = _run()
    wait = ExternalSignalWait(signal_name="answer", correlation_key="question-1")
    create_workflow_run(conn, run, waits=(wait,))

    loaded = require_workflow_run(conn, run.workflow_id)
    assert loaded == run
    assert loaded.definition_version == DEFINITION_VERSION
    assert loaded.revision == 0
    assert loaded.state.schema_name == "static_dag"

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(workflow_runs)")}
    assert {
        "workflow_id",
        "definition_name",
        "definition_version",
        "status",
        "revision",
        "state_json",
        "started_by_json",
        "correlation_json",
        "terminal_reason",
    } <= columns
    assert not columns & {
        "stack",
        "locals",
        "generator",
        "coroutine",
        "code",
        "event_history",
    }


def test_exact_six_wait_variants_and_four_signal_variants_are_durable() -> None:
    conn = _conn()
    run = _run()
    activity_id = uuid4()
    other_activity_id = uuid4()
    approval_id = uuid4()
    timer_id = uuid4()
    waits = (
        ActivityWait(activity_id=activity_id),
        ApprovalWait(approval_id=approval_id),
        TimerWait(timer_id=timer_id, due_at=NOW + timedelta(minutes=5)),
        ExternalSignalWait(signal_name="build.updated", correlation_key="build-7"),
        ResourceWait(resource_kind="worktree", selector={"branch": "main"}),
        JoinWait(activity_ids=(activity_id, other_activity_id), mode="all"),
    )
    create_workflow_run(conn, run, waits=waits)
    assert {record.spec.type for record in list_workflow_waits(conn, run.workflow_id)} == {
        "activity",
        "approval",
        "timer",
        "external_signal",
        "resource",
        "join",
    }

    payloads: tuple[WorkflowSignalPayload, ...] = (
        ActivityFinishedSignal(activity_id=activity_id, result_id=uuid4()),
        ApprovalResolvedSignal(approval_id=approval_id, decision_id=uuid4()),
        TimerFiredSignal(timer_id=timer_id),
        ExternalWorkflowSignal(
            name="build.updated",
            correlation_key="build-7",
            payload={"status": "green"},
        ),
    )
    for index, payload in enumerate(payloads):
        enqueue_workflow_signal(
            conn,
            workflow_id=run.workflow_id,
            deduplication_key=f"signal-{index}",
            payload=payload,
            created_at=NOW + timedelta(seconds=index),
        )

    records = list_workflow_signals(conn, run.workflow_id)
    assert {record.payload.type for record in records} == {
        "activity.finished",
        "approval.resolved",
        "timer.fired",
        "external",
    }
    satisfied = [
        record for record in list_workflow_waits(conn, run.workflow_id) if record.satisfied_at
    ]
    # One completion cannot satisfy an `all` join containing two activities.
    assert {record.spec.type for record in satisfied} == {
        "activity",
        "approval",
        "timer",
        "external_signal",
    }
    assert all(record.satisfied_by_signal_id is not None for record in satisfied)

    enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="join-second",
        payload=ActivityFinishedSignal(
            activity_id=other_activity_id,
            result_id=uuid4(),
        ),
    )
    joins = [
        record
        for record in list_workflow_waits(conn, run.workflow_id)
        if isinstance(record.spec, JoinWait)
    ]
    assert len(joins) == 1
    assert joins[0].satisfied_at is not None


@pytest.mark.parametrize(
    ("mode", "threshold", "completions_needed"),
    [("any", None, 1), ("threshold", 2, 2), ("all", None, 3)],
)
def test_join_wait_respects_mode_and_threshold(
    mode: str,
    threshold: int | None,
    completions_needed: int,
) -> None:
    conn = _conn()
    run = _run()
    activity_ids = (uuid4(), uuid4(), uuid4())
    join = JoinWait(
        activity_ids=activity_ids,
        mode=mode,
        threshold=threshold,
    )
    create_workflow_run(conn, run, waits=(join,))
    for index, activity_id in enumerate(activity_ids):
        enqueue_workflow_signal(
            conn,
            workflow_id=run.workflow_id,
            deduplication_key=f"activity-{index}",
            payload=ActivityFinishedSignal(
                activity_id=activity_id,
                result_id=uuid4(),
            ),
        )
        persisted = list_workflow_waits(conn, run.workflow_id)[0]
        assert bool(persisted.satisfied_at) is (index + 1 >= completions_needed)


def test_signal_creation_is_per_workflow_idempotent_and_collision_safe() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="wake"),),
    )
    payload = ExternalWorkflowSignal(name="wake", payload={"value": 1})
    first = enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="producer:42",
        payload=payload,
        created_at=NOW,
    )
    duplicate = enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="producer:42",
        payload=payload,
        created_at=NOW + timedelta(hours=1),
    )
    assert duplicate == first
    assert len(list_workflow_signals(conn, run.workflow_id)) == 1

    with pytest.raises(SignalDeduplicationConflictError):
        enqueue_workflow_signal(
            conn,
            workflow_id=run.workflow_id,
            deduplication_key="producer:42",
            payload=ExternalWorkflowSignal(name="wake", payload={"value": 2}),
        )


def test_apply_plan_atomically_consumes_replaces_and_increments_revision() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="wake"),),
    )
    signal = enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="wake-once",
        payload=ExternalWorkflowSignal(name="wake"),
        created_at=NOW,
    )
    replacement = ResourceWait(resource_kind="gpu", selector={"minimum": 1})
    plan = WorkflowTransitionPlan(
        state=StateReplacement(
            expected_revision=0,
            status=WorkflowStatus.WAITING,
            state=_state(StageStatus.RUNNING),
        ),
        consume_signal_ids=(signal.signal_id,),
        replace_waits=(replacement,),
        facts=(FactDraft(kind="workflow.progressed", payload={"stage": "one"}),),
    )
    updated = apply_transition_plan(
        conn,
        workflow_id=run.workflow_id,
        plan=plan,
        applied_at=NOW + timedelta(seconds=10),
    )

    assert updated.revision == 1
    assert updated.state == plan.state.state
    consumed = get_workflow_signal(conn, signal.signal_id)
    assert consumed is not None
    assert consumed.consumed_at is not None
    assert consumed.consumed_at_revision == 1
    current_waits = list_workflow_waits(conn, run.workflow_id)
    assert [record.spec for record in current_waits] == [replacement]
    outbox = conn.execute(
        "SELECT kind, workflow_revision FROM workflow_transition_outbox"
    ).fetchall()
    assert [(row["kind"], row["workflow_revision"]) for row in outbox] == [("fact", 1)]


def test_stale_revision_does_not_consume_signal_or_replace_waits() -> None:
    conn = _conn()
    run = _run()
    original_wait = ExternalSignalWait(signal_name="wake")
    create_workflow_run(conn, run, waits=(original_wait,))
    signal = enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="wake",
        payload=ExternalWorkflowSignal(name="wake"),
    )
    plan = WorkflowTransitionPlan(
        state=StateReplacement(
            expected_revision=99,
            status=WorkflowStatus.WAITING,
            state=_state(),
        ),
        consume_signal_ids=(signal.signal_id,),
        replace_waits=(ResourceWait(resource_kind="cpu", selector={}),),
    )

    with pytest.raises(StaleWorkflowRevisionError):
        apply_transition_plan(conn, workflow_id=run.workflow_id, plan=plan)
    assert require_workflow_run(conn, run.workflow_id).revision == 0
    assert get_workflow_signal(conn, signal.signal_id).consumed_at is None  # type: ignore[union-attr]
    assert [record.spec for record in list_workflow_waits(conn, run.workflow_id)] == [original_wait]


def test_apply_plan_rolls_back_every_write_when_wait_replacement_fails() -> None:
    conn = _conn()
    run = _run()
    original_wait = ExternalSignalWait(signal_name="wake")
    create_workflow_run(conn, run, waits=(original_wait,))
    signal = enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="wake",
        payload=ExternalWorkflowSignal(name="wake"),
    )
    conn.executescript(
        """
        CREATE TRIGGER reject_resource_wait
        BEFORE INSERT ON workflow_waits
        WHEN NEW.spec_json LIKE '%"type":"resource"%'
        BEGIN
            SELECT RAISE(ABORT, 'injected wait failure');
        END;
        """
    )
    plan = WorkflowTransitionPlan(
        state=StateReplacement(
            expected_revision=0,
            status=WorkflowStatus.WAITING,
            state=_state(StageStatus.RUNNING),
        ),
        consume_signal_ids=(signal.signal_id,),
        replace_waits=(ResourceWait(resource_kind="gpu", selector={}),),
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected wait failure"):
        apply_transition_plan(conn, workflow_id=run.workflow_id, plan=plan)
    assert require_workflow_run(conn, run.workflow_id).revision == 0
    persisted_signal = get_workflow_signal(conn, signal.signal_id)
    assert persisted_signal is not None and persisted_signal.consumed_at is None
    assert [record.spec for record in list_workflow_waits(conn, run.workflow_id)] == [original_wait]


def test_static_dag_machine_decides_from_state_and_signals_only() -> None:
    definition = WorkflowDef(
        name="two-step",
        definition_version=4,
        stages=[
            StageDef(id="first", title="First", harness="codex", model="gpt-5"),
            StageDef(
                id="second",
                title="Second",
                harness="codex",
                model="gpt-5",
                depends_on=["first"],
            ),
        ],
    )
    stage_map = {"first": "t101", "second": "t102"}
    machine = StaticDagWorkflowMachine(definition, stage_map)
    state = machine.initialize(inputs={"topic": "x"}, now=NOW)
    run_id = uuid4()
    signal = ExternalWorkflowSignal(
        name="ticket.finished",
        correlation_key="t101",
        payload={"status": "done"},
    )
    signal_record = WorkflowSignalRecord(
        signal_id=uuid4(),
        workflow_id=run_id,
        deduplication_key="ticket:t101:done",
        created_at=NOW,
        payload=signal,
    )
    plan = machine.decide(
        state=state,
        waits=(),
        signals=(signal_record,),
        now=NOW,
        current_revision=DECISION_REVISION,
    )

    decided = StaticDagWorkflowStateV1.model_validate(plan.state.state.value)
    assert plan.state.expected_revision == DECISION_REVISION
    assert plan.state.status == WorkflowStatus.WAITING
    assert [(stage.stage_id, stage.status) for stage in decided.stages] == [
        ("first", StageStatus.SUCCEEDED),
        ("second", StageStatus.READY),
    ]
    assert plan.consume_signal_ids == (signal_record.signal_id,)
    assert [
        wait.correlation_key for wait in plan.replace_waits if isinstance(wait, ExternalSignalWait)
    ] == ["t102"]
    # Inputs and outputs are immutable values; attempting mutation is rejected.
    with pytest.raises(ValidationError):
        plan.state.expected_revision = 8


def test_load_decision_input_is_finite_current_state_not_history_replay() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="wake"),),
    )
    for index in range(3):
        enqueue_workflow_signal(
            conn,
            workflow_id=run.workflow_id,
            deduplication_key=f"wake-{index}",
            payload=ExternalWorkflowSignal(name="wake", payload={"index": index}),
        )
    decision = load_workflow_decision_input(
        conn,
        run.workflow_id,
        now=NOW,
        signal_limit=FINITE_SIGNAL_LIMIT,
    )
    assert decision.run == run
    assert len(decision.waits) == 1
    assert len(decision.signals) == FINITE_SIGNAL_LIMIT
    assert decision.now == NOW
