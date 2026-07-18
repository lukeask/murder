from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import ValidationError

from murder.facts.log import get_fact, replay_facts, replay_projection_inputs
from murder.permissions import (
    ApprovalChoice,
    GrantScope,
    PermissionPrincipal,
)
from murder.state.persistence.approvals import resolve_approval_request
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
    ApprovalRequestDraft,
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
    assert outbox == []
    facts = conn.execute("SELECT fact_id, kind FROM retained_facts ORDER BY sequence").fetchall()
    assert [row["kind"] for row in facts] == [
        "workflow.started",
        "workflow.transition.applied",
        "workflow.progressed",
    ]
    fact_row = next(row for row in facts if row["kind"] == "workflow.progressed")
    fact = get_fact(conn, UUID(str(fact_row["fact_id"])))
    assert fact is not None
    assert fact.kind == "workflow.progressed"
    assert fact.aggregate is not None
    assert fact.aggregate.kind == "workflow"
    assert fact.aggregate.id == run.workflow_id
    assert fact.aggregate.revision == 1
    assert fact.actor.kind == "user"
    assert fact.actor.id == "luke"
    assert fact.correlation.correlation_id == run.correlation.correlation_id
    assert fact.payload == {"stage": "one"}
    projection_inputs = replay_projection_inputs(conn, projection="workflow_runs")
    _started_input, transition_input = projection_inputs
    transition_fact = get_fact(conn, transition_input.source_fact_id)
    assert transition_fact is not None
    assert transition_fact.kind == "workflow.transition.applied"
    assert (
        transition_input.subject_key,
        transition_input.generation,
    ) == (str(run.workflow_id), 1)


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


def test_approval_creation_resolution_grant_signal_and_fact_are_atomic() -> None:
    conn = _conn()
    run = _run(status=WorkflowStatus.RUNNING)
    create_workflow_run(conn, run)
    approval_id = uuid4()
    digest = hashlib.sha256(b"exact proposed operation").hexdigest()
    draft = ApprovalRequestDraft(
        approval_id=approval_id,
        operation_digest=digest,
        summary="destructive operation",
        required_reviewers=("human",),
        policy="human_required",
        requested_by=PermissionPrincipal(kind="workflow", id=str(run.workflow_id)),
        grant_scope=GrantScope(
            workflow_ids=(run.workflow_id,),
            operation_types=("git.mutate",),
            max_uses=1,
            expires_at=NOW + timedelta(minutes=10),
        ),
    )
    apply_transition_plan(
        conn,
        workflow_id=run.workflow_id,
        plan=WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=0,
                status=WorkflowStatus.WAITING,
                state=_state(),
            ),
            replace_waits=(ApprovalWait(approval_id=approval_id),),
            approvals=(draft,),
        ),
        applied_at=NOW,
    )
    row = conn.execute(
        "SELECT payload_json FROM permission_approval_requests WHERE approval_id = ?",
        (str(approval_id),),
    ).fetchone()
    assert row is not None

    with pytest.raises(ValueError, match="digest"):
        resolve_approval_request(
            conn,
            workflow_id=run.workflow_id,
            approval_id=approval_id,
            expected_workflow_revision=1,
            expected_operation_digest="0" * 64,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.APPROVE,
            rationale="looks safe",
            decided_at=NOW + timedelta(seconds=1),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM permission_approval_evidence"
    ).fetchone()[0] == 0
    assert len(replay_facts(conn, kind="permission.approval.requested")) == 1
    assert len(replay_projection_inputs(conn, projection="approvals")) == 1

    with pytest.raises(ValueError, match="cannot review"):
        resolve_approval_request(
            conn,
            workflow_id=run.workflow_id,
            approval_id=approval_id,
            expected_workflow_revision=1,
            expected_operation_digest=digest,
            reviewer=PermissionPrincipal(kind="service", id="scheduler"),
            choice=ApprovalChoice.APPROVE,
            rationale="self-approved",
            decided_at=NOW + timedelta(seconds=1),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM permission_approval_evidence"
    ).fetchone()[0] == 0

    conn.execute(
        """
        UPDATE workflow_runs SET status = 'cancelled', revision = 2
        WHERE workflow_id = ?
        """,
        (str(run.workflow_id),),
    )
    with pytest.raises(ValueError, match="current workflow revision"):
        resolve_approval_request(
            conn,
            workflow_id=run.workflow_id,
            approval_id=approval_id,
            expected_workflow_revision=1,
            expected_operation_digest=digest,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.APPROVE,
            rationale="too late",
            decided_at=NOW + timedelta(seconds=1),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM permission_authorization_grants"
    ).fetchone()[0] == 0
    conn.execute(
        """
        UPDATE workflow_runs SET status = 'waiting', revision = 1
        WHERE workflow_id = ?
        """,
        (str(run.workflow_id),),
    )

    conn.execute(
        """
        CREATE TRIGGER reject_permission_grant_fact
        BEFORE INSERT ON retained_facts
        WHEN NEW.kind = 'permission.grant.issued'
        BEGIN
            SELECT RAISE(ABORT, 'reject grant fact');
        END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="reject grant fact"):
        resolve_approval_request(
            conn,
            workflow_id=run.workflow_id,
            approval_id=approval_id,
            expected_workflow_revision=1,
            expected_operation_digest=digest,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.APPROVE,
            rationale="must roll back",
            decided_at=NOW + timedelta(seconds=1),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM permission_approval_evidence"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM permission_authorization_grants"
    ).fetchone()[0] == 0
    persisted_request = conn.execute(
        """
        SELECT status FROM permission_approval_requests
        WHERE approval_id = ?
        """,
        (str(approval_id),),
    ).fetchone()
    assert persisted_request["status"] == "pending"
    conn.execute("DROP TRIGGER reject_permission_grant_fact")

    decision, grant, authorization = resolve_approval_request(
        conn,
        workflow_id=run.workflow_id,
        approval_id=approval_id,
        expected_workflow_revision=1,
        expected_operation_digest=digest,
        reviewer=PermissionPrincipal(kind="reviewer", id="human"),
        choice=ApprovalChoice.APPROVE,
        rationale="looks safe",
        decided_at=NOW + timedelta(seconds=2),
    )
    assert grant is not None
    assert grant.operation_digest == digest
    assert authorization is not None
    assert authorization.operation_digest == digest
    assert authorization.grant_id == grant.grant_id
    assert authorization.operation_id == approval_id
    signals = list_workflow_signals(conn, run.workflow_id)
    assert any(
        isinstance(item.payload, ApprovalResolvedSignal)
        and item.payload.approval_id == approval_id
        and item.payload.decision_id == decision.decision_id
        for item in signals
    )
    wait = next(
        item for item in list_workflow_waits(conn, run.workflow_id)
        if isinstance(item.spec, ApprovalWait)
    )
    assert wait.satisfied_at is not None
    facts = replay_facts(conn, kind="permission.approval.resolved")
    assert len(facts) == 1
    assert facts[0].payload["operation_digest"] == digest
    approval_inputs = replay_projection_inputs(conn, projection="approvals")
    assert [item.source_fact_id for item in approval_inputs] == [
        uuid5(NAMESPACE_URL, f"murder:approval-requested:{approval_id}"),
        uuid5(
            NAMESPACE_URL,
            f"murder:approval-resolved:{approval_id}:{decision.decision_id}",
        ),
    ]


def test_approval_deny_and_partial_resolution_yield_no_authorization() -> None:
    conn = _conn()
    run = _run(status=WorkflowStatus.RUNNING)
    create_workflow_run(conn, run)
    deny_id = uuid4()
    partial_id = uuid4()
    digest = hashlib.sha256(b"workflow approval without proposed operation").hexdigest()
    deny_draft = ApprovalRequestDraft(
        approval_id=deny_id,
        operation_digest=digest,
        summary="deny me",
        required_reviewers=("human",),
        policy="human_required",
        requested_by=PermissionPrincipal(kind="workflow", id=str(run.workflow_id)),
        grant_scope=GrantScope(
            workflow_ids=(run.workflow_id,),
            operation_types=("git.mutate",),
            max_uses=1,
            expires_at=NOW + timedelta(minutes=10),
        ),
    )
    partial_draft = ApprovalRequestDraft(
        approval_id=partial_id,
        operation_digest=digest,
        summary="need both reviewers",
        required_reviewers=("human", "llm"),
        policy="all",
        requested_by=PermissionPrincipal(kind="workflow", id=str(run.workflow_id)),
        grant_scope=GrantScope(
            workflow_ids=(run.workflow_id,),
            operation_types=("git.mutate",),
            max_uses=1,
            expires_at=NOW + timedelta(minutes=10),
        ),
    )
    apply_transition_plan(
        conn,
        workflow_id=run.workflow_id,
        plan=WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=0,
                status=WorkflowStatus.WAITING,
                state=_state(),
            ),
            replace_waits=(
                ApprovalWait(approval_id=deny_id),
                ApprovalWait(approval_id=partial_id),
            ),
            approvals=(deny_draft, partial_draft),
        ),
        applied_at=NOW,
    )

    _decision, grant, authorization = resolve_approval_request(
        conn,
        workflow_id=run.workflow_id,
        approval_id=deny_id,
        expected_workflow_revision=1,
        expected_operation_digest=digest,
        reviewer=PermissionPrincipal(kind="reviewer", id="human"),
        choice=ApprovalChoice.DENY,
        rationale="not safe",
        decided_at=NOW + timedelta(seconds=1),
    )
    assert grant is None
    assert authorization is None

    _decision, grant, authorization = resolve_approval_request(
        conn,
        workflow_id=run.workflow_id,
        approval_id=partial_id,
        expected_workflow_revision=1,
        expected_operation_digest=digest,
        reviewer=PermissionPrincipal(kind="reviewer", id="human"),
        choice=ApprovalChoice.APPROVE,
        rationale="human half",
        decided_at=NOW + timedelta(seconds=2),
    )
    assert grant is None
    assert authorization is None
    assert (
        conn.execute(
            "SELECT status FROM permission_approval_requests WHERE approval_id = ?",
            (str(partial_id),),
        ).fetchone()["status"]
        == "pending"
    )


def test_factless_transition_still_invalidates_workflow_projection_transactionally() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="wake"),),
    )
    apply_transition_plan(
        conn,
        workflow_id=run.workflow_id,
        plan=WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=0,
                status=WorkflowStatus.WAITING,
                state=_state(StageStatus.RUNNING),
            ),
            replace_waits=(ExternalSignalWait(signal_name="next"),),
        ),
        applied_at=NOW,
    )

    facts = conn.execute("SELECT kind FROM retained_facts ORDER BY sequence").fetchall()
    assert [row["kind"] for row in facts] == [
        "workflow.started",
        "workflow.transition.applied",
    ]
    inputs = replay_projection_inputs(conn, projection="workflow_runs")
    assert [(item.subject_key, item.generation) for item in inputs] == [
        (str(run.workflow_id), 0),
        (str(run.workflow_id), 1),
    ]


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
