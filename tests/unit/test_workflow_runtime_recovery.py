from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import JsonValue

import murder.work.workflows.service as workflow_service
from murder.state.persistence.schema import init_db
from murder.state.persistence.workflow_runs import (
    StaleWorkflowRevisionError,
    TerminalWorkflowTransitionError,
    WorkflowStateMigrationRequiredError,
    apply_transition_plan,
    create_workflow_run,
    enqueue_workflow_signal,
    list_workflow_signals,
    list_workflow_state_migrations,
    require_workflow_run,
)
from murder.work.workflows.definition import StageDef, WorkflowDef
from murder.work.workflows.runtime import (
    Correlation,
    ExternalSignalWait,
    ExternalWorkflowSignal,
    PrincipalKind,
    PrincipalRef,
    StageRunState,
    StageStatus,
    StateReplacement,
    StaticDagWorkflowStateV1,
    VersionedState,
    WorkflowContract,
    WorkflowRunRecord,
    WorkflowSignalRecord,
    WorkflowStatus,
    WorkflowTransitionPlan,
    WorkflowWaitRecord,
    versioned_state,
)
from murder.work.workflows.service import (
    WorkflowDefinitionUnavailableError,
    WorkflowMachineKey,
    WorkflowMachineRegistry,
    WorkflowRuntime,
    resolve_persisted_machine,
)

NOW = datetime(2026, 7, 18, 18, 0, tzinfo=timezone.utc)
SECOND_REVISION = 2


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def _static_run() -> WorkflowRunRecord:
    definition = WorkflowDef(
        name="recovery-probe",
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
    state = StaticDagWorkflowStateV1(
        stages=(
            StageRunState(stage_id="first", status=StageStatus.READY),
            StageRunState(stage_id="second", status=StageStatus.BLOCKED),
        )
    )
    return WorkflowRunRecord(
        workflow_id=uuid4(),
        definition_name=definition.name,
        definition_version=definition.definition_version,
        status=WorkflowStatus.WAITING,
        revision=0,
        state=versioned_state(state, schema_name="static_dag", schema_version=1),
        created_at=NOW,
        updated_at=NOW,
        started_by=PrincipalRef(kind=PrincipalKind.SERVICE, id="test"),
        correlation=Correlation(correlation_id=uuid4()),
        definition_snapshot=definition.model_dump(mode="json"),
        stage_map={"first": "t100", "second": "t101"},
    )


def _create_static_run(conn: sqlite3.Connection) -> WorkflowRunRecord:
    run = _static_run()
    create_workflow_run(
        conn,
        run,
        waits=(
            ExternalSignalWait(signal_name="ticket.finished", correlation_key="t100"),
            ExternalSignalWait(signal_name="ticket.finished", correlation_key="t101"),
        ),
    )
    return run


def test_unknown_state_schema_version_is_rejected() -> None:
    run = _static_run()
    unknown = run.model_copy(update={"state": run.state.model_copy(update={"schema_version": 2})})
    with pytest.raises(WorkflowDefinitionUnavailableError):
        resolve_persisted_machine(unknown)


class StateV2(WorkflowContract):
    count: int


class StateV2Machine:
    definition_name = "recovery-probe"
    definition_version = 1
    state_model = StateV2

    def initialize(
        self,
        *,
        inputs: dict[str, JsonValue],
        now: datetime,
    ) -> StateV2:
        del inputs, now
        return StateV2(count=0)

    def decide(
        self,
        *,
        state: StateV2,
        waits: tuple[WorkflowWaitRecord, ...],
        signals: tuple[WorkflowSignalRecord, ...],
        now: datetime,
        current_revision: int,
    ) -> WorkflowTransitionPlan:
        del waits, signals, now
        return WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=current_revision,
                status=WorkflowStatus.WAITING,
                state=VersionedState(
                    schema_name="probe_state",
                    schema_version=2,
                    value=state.model_dump(mode="json"),
                ),
            ),
            replace_waits=(ExternalSignalWait(signal_name="continue"),),
        )


def test_explicit_registered_state_migration_is_recorded() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    registry = WorkflowMachineRegistry()
    registry.register(
        WorkflowMachineKey(
            run.definition_name,
            run.definition_version,
            "probe_state",
            2,
        ),
        StateV2Machine(),
    )
    runtime = WorkflowRuntime(conn, resolver=registry.resolve)
    record = runtime.migrate_state(
        run.workflow_id,
        expected_revision=0,
        target_state=VersionedState(
            schema_name="probe_state",
            schema_version=2,
            value={"count": 7},
        ),
        migration_name="static-dag-to-probe-v2",
        now=NOW,
    )
    migrated = require_workflow_run(conn, run.workflow_id)
    assert migrated.revision == 1
    assert migrated.state.schema_name == "probe_state"
    assert record.from_schema_name == "static_dag"
    assert record.to_schema_version == SECOND_REVISION
    assert list_workflow_state_migrations(conn, run.workflow_id) == (record,)


def test_normal_decision_cannot_bypass_recorded_state_migration() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    plan = WorkflowTransitionPlan(
        state=StateReplacement(
            expected_revision=0,
            status=WorkflowStatus.WAITING,
            state=VersionedState(
                schema_name="unrecorded",
                schema_version=2,
                value={"count": 1},
            ),
        ),
        replace_waits=(ExternalSignalWait(signal_name="continue"),),
    )

    with pytest.raises(WorkflowStateMigrationRequiredError):
        apply_transition_plan(conn, workflow_id=run.workflow_id, plan=plan, applied_at=NOW)

    persisted = require_workflow_run(conn, run.workflow_id)
    assert persisted.revision == 0
    assert list_workflow_state_migrations(conn, run.workflow_id) == ()


def test_pending_signal_recovery_wakes_after_restart() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="ticket:t100:terminal:done",
        payload=ExternalWorkflowSignal(
            name="ticket.finished",
            correlation_key="t100",
            payload={"status": "done"},
        ),
        created_at=NOW,
    )
    recovered = WorkflowRuntime(conn).recover_pending_signals(now=NOW)
    assert len(recovered) == 1
    assert recovered[0].revision == 1
    assert list_workflow_signals(conn, run.workflow_id) == []


def test_pending_signal_recovery_drains_every_workflow_page() -> None:
    conn = _conn()
    runs = [_create_static_run(conn) for _ in range(3)]
    for run in runs:
        enqueue_workflow_signal(
            conn,
            workflow_id=run.workflow_id,
            deduplication_key=f"ticket:{run.workflow_id}:done",
            payload=ExternalWorkflowSignal(
                name="ticket.finished",
                correlation_key="t100",
                payload={"status": "done"},
            ),
            created_at=NOW,
        )

    recovered = WorkflowRuntime(conn).recover_pending_signals(limit=2, now=NOW)

    assert {record.workflow_id for record in recovered} == {run.workflow_id for run in runs}
    assert all(not list_workflow_signals(conn, run.workflow_id) for run in runs)


def test_pending_signal_without_transition_is_consumed_once() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="external:not-for-static-dag",
        payload=ExternalWorkflowSignal(name="unhandled"),
        created_at=NOW,
    )

    first = WorkflowRuntime(conn).recover_pending_signals(now=NOW)
    second = WorkflowRuntime(conn).recover_pending_signals(now=NOW)

    assert first[0].revision == 1
    assert second == ()
    assert list_workflow_signals(conn, run.workflow_id) == []


def test_enqueue_and_wake_persists_then_decides() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    signal, updated = WorkflowRuntime(conn).enqueue_and_wake(
        workflow_id=run.workflow_id,
        deduplication_key="ticket:t100:terminal:done",
        payload=ExternalWorkflowSignal(
            name="ticket.finished",
            correlation_key="t100",
            payload={"status": "done"},
        ),
        created_at=NOW,
    )
    assert signal.deduplication_key == "ticket:t100:terminal:done"
    assert updated.revision == 1
    assert list_workflow_signals(conn, run.workflow_id) == []


def test_conflict_retry_reloads_and_decides_again(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    run = _create_static_run(conn)
    enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="ticket:t100:terminal:done",
        payload=ExternalWorkflowSignal(
            name="ticket.finished",
            correlation_key="t100",
            payload={"status": "done"},
        ),
    )
    real_apply = workflow_service.apply_transition_plan
    calls = 0

    def conflict_once(*args: object, **kwargs: object) -> WorkflowRunRecord:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StaleWorkflowRevisionError(run.workflow_id, 0, 1)
        return real_apply(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(workflow_service, "apply_transition_plan", conflict_once)
    updated = WorkflowRuntime(conn, max_conflict_retries=1).decide_once(run.workflow_id)
    assert calls == SECOND_REVISION
    assert updated.revision == 1


def test_conflict_retry_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    run = _create_static_run(conn)
    enqueue_workflow_signal(
        conn,
        workflow_id=run.workflow_id,
        deduplication_key="ticket:t100:terminal:done",
        payload=ExternalWorkflowSignal(
            name="ticket.finished",
            correlation_key="t100",
            payload={"status": "done"},
        ),
    )
    calls = 0

    def always_conflict(*args: object, **kwargs: object) -> WorkflowRunRecord:
        del args, kwargs
        nonlocal calls
        calls += 1
        raise StaleWorkflowRevisionError(run.workflow_id, 0, 1)

    monkeypatch.setattr(workflow_service, "apply_transition_plan", always_conflict)
    with pytest.raises(StaleWorkflowRevisionError):
        WorkflowRuntime(conn, max_conflict_retries=1).decide_once(run.workflow_id)
    assert calls == SECOND_REVISION


def test_repeated_ticket_signal_is_stably_deduplicated() -> None:
    conn = _conn()
    run = _create_static_run(conn)
    runtime = WorkflowRuntime(conn)
    first = runtime.signal_ticket_finished(ticket_id="t100", status="done", occurred_at=NOW)[0]
    duplicate = runtime.signal_ticket_finished(
        ticket_id="t100",
        status="done",
        occurred_at=NOW,
    )[0]
    assert first.revision == duplicate.revision == 1
    all_signals = list_workflow_signals(
        conn,
        run.workflow_id,
        include_consumed=True,
    )
    assert len(all_signals) == 1


def test_late_signal_cannot_reopen_or_advance_terminal_workflow() -> None:
    conn = _conn()
    run = _static_run().model_copy(
        update={
            "status": WorkflowStatus.COMPLETED,
            "terminal_reason": "already finished",
        }
    )
    create_workflow_run(conn, run)
    runtime = WorkflowRuntime(conn)

    signal, unchanged = runtime.enqueue_and_wake(
        workflow_id=run.workflow_id,
        deduplication_key="late",
        payload=ExternalWorkflowSignal(name="late"),
        created_at=NOW,
    )

    assert signal.consumed_at is None
    assert unchanged == run
    plan = WorkflowTransitionPlan(
        state=StateReplacement(
            expected_revision=0,
            status=WorkflowStatus.WAITING,
            state=run.state,
        ),
        replace_waits=(ExternalSignalWait(signal_name="anything"),),
    )
    with pytest.raises(TerminalWorkflowTransitionError):
        apply_transition_plan(conn, workflow_id=run.workflow_id, plan=plan, applied_at=NOW)
    assert require_workflow_run(conn, run.workflow_id) == run
