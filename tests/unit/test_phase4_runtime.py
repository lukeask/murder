from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from murder.app.service.runtime import Runtime
from murder.facts.contracts import FactActor, FactCorrelation, RetainedFactDraft
from murder.facts.log import append_fact
from murder.runtime.activity_dispatcher import ActivityDispatcher
from murder.runtime.admission import (
    AdmissionContext,
    Admitted,
    Deferred,
    Rejected,
    decide_admission,
)
from murder.runtime.trigger_dispatcher import (
    TriggerDispatcher,
    build_default_trigger_dispatcher,
)
from murder.state.persistence.activities import (
    ActivityLifecycleError,
    claim_activity,
    complete_activity,
    get_activity,
    persist_admission,
    persist_route,
    reap_expired_reservations,
    renew_activity_claim,
    start_activity,
)
from murder.state.persistence.schema import init_db
from murder.state.persistence.triggers import create_trigger, fire_trigger
from murder.state.persistence.workflow_runs import (
    apply_transition_plan,
    create_workflow_run,
    list_workflow_signals,
    require_workflow_run,
)
from murder.work.activities.runtime import (
    ActivityFailure,
    ActivityStatus,
    ActivitySuccess,
    ModelAssignment,
)
from murder.work.routing import RouteCandidate, RoutingContext, decide_route
from murder.work.triggers.runtime import (
    CronTrigger,
    FactTrigger,
    ManualTrigger,
    RepositoryTrigger,
    SignalWorkflowTarget,
    StartWorkflowTarget,
    TriggerDefinition,
)
from murder.work.workflows.definition import StageDef, WorkflowDef
from murder.work.workflows.runtime import (
    ActivityRequestDraft,
    ActivityWait,
    Correlation,
    ExecutionRequirements,
    ExternalSignalWait,
    PrincipalKind,
    PrincipalRef,
    RunAgentTurnActivity,
    StageRunState,
    StageStatus,
    StateReplacement,
    StaticDagWorkflowStateV1,
    WorkflowRunRecord,
    WorkflowStatus,
    WorkflowTransitionPlan,
    versioned_state,
)

NOW = datetime(2026, 7, 18, 20, 0, tzinfo=timezone.utc)
TRIGGER_SPEC_COUNT = 4
EXPECTED_TRANSIENT_TICKS = 2
REPOSITORY_DEBOUNCE_SECONDS = 30
MODEL_ASSIGNMENT_ROLES = (
    "primary",
    "planner",
    "reviewer",
    "critic",
    "summarizer",
    "specialist",
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def _run() -> WorkflowRunRecord:
    definition = WorkflowDef(
        name="phase4",
        stages=[StageDef(id="work", title="Work", harness="codex", model="gpt-5")],
    )
    state = StaticDagWorkflowStateV1(
        stages=(StageRunState(stage_id="work", status=StageStatus.READY),)
    )
    return WorkflowRunRecord(
        workflow_id=uuid4(),
        definition_name=definition.name,
        definition_version=1,
        status=WorkflowStatus.WAITING,
        revision=0,
        state=versioned_state(state, schema_name="static_dag", schema_version=1),
        created_at=NOW,
        updated_at=NOW,
        started_by=PrincipalRef(kind=PrincipalKind.SERVICE, id="test"),
        correlation=Correlation(correlation_id=uuid4()),
        definition_snapshot=definition.model_dump(mode="json"),
        stage_map={"work": "t100"},
    )


def _create_activity(conn: sqlite3.Connection) -> tuple[WorkflowRunRecord, UUID]:
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="initial"),),
    )
    activity_id = uuid4()
    requirements = ExecutionRequirements(
        capability_tags=frozenset({"coding"}),
        preferred_harnesses=("codex",),
        preferred_models=("gpt-5",),
        require_structured_protocol=True,
    )
    apply_transition_plan(
        conn,
        workflow_id=run.workflow_id,
        plan=WorkflowTransitionPlan(
            state=StateReplacement(
                expected_revision=0,
                status=WorkflowStatus.WAITING,
                state=run.state,
            ),
            replace_waits=(ActivityWait(activity_id=activity_id),),
            activities=(
                ActivityRequestDraft(
                    activity_id=activity_id,
                    payload=RunAgentTurnActivity(
                        instructions="implement",
                        requirements=requirements,
                    ),
                    idempotency_key=f"activity:{activity_id}",
                ),
            ),
        ),
        applied_at=NOW,
    )
    return run, activity_id


def _route_and_admit(
    conn: sqlite3.Connection,
    activity_id: UUID,
    *,
    now: datetime = NOW,
    lock: str = "worktree:main",
) -> Admitted:
    activity = get_activity(conn, activity_id)
    assert activity is not None
    routing = decide_route(
        RoutingContext(
            activity_id=activity_id,
            requirements=activity.requirements,
            candidates=(
                RouteCandidate(
                    harness="codex",
                    models=("gpt-5",),
                    capability_tags=frozenset({"coding"}),
                    structured_protocol=True,
                    capability_revision=7,
                ),
            ),
        )
    )
    assert routing.route is not None
    routed = persist_route(conn, activity_id, routing.route, now=now)
    decision = decide_admission(
        AdmissionContext(
            activity=routed,
            running_total=0,
            max_running=2,
            repository="repo",
            queued_at=NOW,
            now=now,
            required_locks=frozenset({lock}),
        )
    )
    assert isinstance(decision, Admitted)
    persist_admission(conn, activity_id, decision, now=now)
    return decision


def test_transition_creates_exact_waited_activity_atomically() -> None:
    conn = _conn()
    run, activity_id = _create_activity(conn)
    activity = get_activity(conn, activity_id)
    assert activity is not None
    assert activity.workflow_id == run.workflow_id
    assert activity.workflow_revision == 1
    assert activity.status == ActivityStatus.PENDING
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM workflow_transition_outbox WHERE kind = 'activity'"
    ).fetchone()["n"] == 0
    duplicate_id = uuid4()
    with pytest.raises(sqlite3.IntegrityError):
        apply_transition_plan(
            conn,
            workflow_id=run.workflow_id,
            plan=WorkflowTransitionPlan(
                state=StateReplacement(
                    expected_revision=1,
                    status=WorkflowStatus.WAITING,
                    state=run.state,
                ),
                replace_waits=(ActivityWait(activity_id=duplicate_id),),
                activities=(
                    ActivityRequestDraft(
                        activity_id=duplicate_id,
                        payload=activity.payload,
                        idempotency_key=activity.idempotency_key,
                    ),
                ),
            ),
            applied_at=NOW,
        )
    assert require_workflow_run(conn, run.workflow_id).revision == 1


def test_route_roles_and_repository_trigger_contract() -> None:
    for role in MODEL_ASSIGNMENT_ROLES:
        assignment = ModelAssignment(role=role, harness="codex", model="gpt-5")
        assert assignment.role == role

    repository_id = uuid4()
    trigger = RepositoryTrigger(
        repository_id=repository_id,
        debounce_seconds=REPOSITORY_DEBOUNCE_SECONDS,
    )
    assert trigger.repository_id == repository_id
    assert trigger.debounce_seconds == REPOSITORY_DEBOUNCE_SECONDS
    with pytest.raises(ValueError):
        RepositoryTrigger(repository_id=repository_id, debounce_seconds=-1)


def test_route_admit_fenced_claim_renew_and_complete() -> None:
    conn = _conn()
    run, activity_id = _create_activity(conn)
    activity = get_activity(conn, activity_id)
    assert activity is not None
    routing = decide_route(
        RoutingContext(
            activity_id=activity_id,
            requirements=activity.requirements,
            candidates=(
                RouteCandidate(
                    harness="codex",
                    models=("gpt-5",),
                    capability_tags=frozenset({"coding"}),
                    structured_protocol=True,
                    capability_revision=7,
                ),
            ),
        )
    )
    assert routing.route is not None
    routed = persist_route(conn, activity_id, routing.route, now=NOW)
    decision = decide_admission(
        AdmissionContext(
            activity=routed,
            running_total=0,
            max_running=2,
            repository="repo",
            queued_at=NOW,
            now=NOW,
            required_locks=frozenset({"worktree:main"}),
        )
    )
    assert isinstance(decision, Admitted)
    persist_admission(conn, activity_id, decision, now=NOW)
    claim = claim_activity(
        conn,
        activity_id,
        owner="worker-1",
        lease_for=timedelta(minutes=2),
        capability_revision=7,
        now=NOW,
    )
    claim = renew_activity_claim(
        conn,
        claim,
        lease_for=timedelta(minutes=3),
        now=NOW + timedelta(seconds=10),
    )
    start_activity(conn, claim, now=NOW + timedelta(seconds=20))
    with pytest.raises(ActivityLifecycleError):
        complete_activity(
            conn,
            claim.model_copy(update={"fence": claim.fence + 1}),
            ActivitySuccess(output={"ok": True}),
            now=NOW + timedelta(seconds=30),
        )
    result = complete_activity(
        conn,
        claim,
        ActivitySuccess(output={"ok": True}),
        now=NOW + timedelta(seconds=30),
    )
    assert (
        complete_activity(
            conn,
            claim,
            ActivitySuccess(output={"ok": True}),
            now=NOW + timedelta(seconds=31),
        )
        == result
    )
    completed = get_activity(conn, activity_id)
    assert completed is not None and completed.status == ActivityStatus.SUCCEEDED
    assert conn.execute(
        "SELECT released_at FROM activity_reservations WHERE activity_id = ?",
        (str(activity_id),),
    ).fetchone()["released_at"] is not None
    assert conn.execute(
        "SELECT kind FROM retained_facts WHERE fact_id = ?",
        (str(result.result_id),),
    ).fetchone()["kind"] == "activity.succeeded"
    projection_rows = conn.execute(
        """
        SELECT source_fact_id, generation
          FROM projection_inputs
         WHERE projection = 'activities' AND subject_key = ?
         ORDER BY sequence
        """,
        (str(activity_id),),
    ).fetchall()
    assert [row["generation"] for row in projection_rows] == list(range(7))
    assert all(row["source_fact_id"] is not None for row in projection_rows)
    assert projection_rows[-1]["source_fact_id"] == str(result.result_id)
    lifecycle_kinds = [
        str(row["kind"])
        for row in conn.execute(
            """
            SELECT kind FROM retained_facts
             WHERE kind LIKE 'activity.%'
             ORDER BY sequence
            """
        ).fetchall()
    ]
    assert lifecycle_kinds == [
        "activity.created",
        "activity.routed",
        "activity.admitted",
        "activity.claimed",
        "activity.claim_renewed",
        "activity.started",
        "activity.succeeded",
    ]
    assert any(
        signal.payload.type == "activity.finished"
        for signal in list_workflow_signals(
            conn,
            run.workflow_id,
            include_consumed=True,
        )
    )


def test_lock_expiry_and_retryable_attempt_results() -> None:
    conn = _conn()
    run, first_id = _create_activity(conn)
    _, second_id = _create_activity(conn)
    _route_and_admit(conn, first_id)
    with pytest.raises(ActivityLifecycleError, match="already reserved"):
        _route_and_admit(conn, second_id)

    claim = claim_activity(
        conn,
        first_id,
        owner="worker",
        lease_for=timedelta(minutes=5),
        capability_revision=7,
        now=NOW,
    )
    retry = complete_activity(
        conn,
        claim,
        ActivityFailure(
            code="transient",
            message="try again",
            retryable=True,
        ),
        now=NOW + timedelta(seconds=10),
    )
    assert get_activity(conn, first_id).status == ActivityStatus.WAITING_ADMISSION
    _route_and_admit(conn, second_id, now=NOW + timedelta(seconds=20))
    assert reap_expired_reservations(conn, now=NOW + timedelta(minutes=2)) == 1
    _route_and_admit(conn, first_id, now=NOW + timedelta(minutes=2))
    second_claim = claim_activity(
        conn,
        first_id,
        owner="worker",
        lease_for=timedelta(minutes=5),
        capability_revision=7,
        now=NOW + timedelta(minutes=2),
    )
    success = complete_activity(
        conn,
        second_claim,
        ActivitySuccess(output={"done": True}),
        now=NOW + timedelta(minutes=2, seconds=10),
    )
    rows = conn.execute(
        "SELECT attempt FROM activity_results WHERE activity_id = ? ORDER BY attempt",
        (str(first_id),),
    ).fetchall()
    assert [row["attempt"] for row in rows] == [retry.attempt, success.attempt] == [1, 2]
    signals = list_workflow_signals(conn, run.workflow_id, include_consumed=True)
    assert len([signal for signal in signals if signal.payload.type == "activity.finished"]) == 1


def test_trigger_signal_firing_is_atomic_and_deduplicated() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="manual"),),
    )
    trigger = TriggerDefinition(
        trigger_id=uuid4(),
        name="manual-run",
        version=1,
        spec=ManualTrigger(command="run"),
        target=SignalWorkflowTarget(
            workflow_id=run.workflow_id,
            signal_name="manual",
        ),
        dedup_window_seconds=60,
        created_at=NOW,
    )
    create_trigger(conn, trigger)

    def no_start(
        connection: sqlite3.Connection,
        target: object,
        now: datetime,
    ) -> UUID:
        del connection, target, now
        raise AssertionError("signal target must not start a workflow")

    first = fire_trigger(
        conn,
        trigger.trigger_id,
        occurrence_key="click-1",
        start_workflow=no_start,
        now=NOW,
    )
    duplicate = fire_trigger(
        conn,
        trigger.trigger_id,
        occurrence_key="click-2",
        start_workflow=no_start,
        now=NOW,
    )
    assert first == duplicate
    assert conn.execute("SELECT COUNT(*) AS n FROM trigger_firings").fetchone()["n"] == 1


def test_trigger_started_workflow_is_woken_and_progresses() -> None:
    conn = _conn()
    trigger = TriggerDefinition(
        trigger_id=uuid4(),
        name="starter",
        version=1,
        spec=ManualTrigger(command="run"),
        target=StartWorkflowTarget(definition_name="phase4", definition_version=1),
        created_at=NOW,
    )
    create_trigger(conn, trigger)

    def start(
        connection: sqlite3.Connection,
        target: StartWorkflowTarget,
        now: datetime,
    ) -> UUID:
        del target, now
        # A resolvable run: static_dag state + persisted definition snapshot.
        run = _run()
        create_workflow_run(
            connection,
            run,
            waits=(
                ExternalSignalWait(
                    signal_name="ticket.finished",
                    correlation_key="t100",
                ),
            ),
        )
        return run.workflow_id

    firing = fire_trigger(
        conn,
        trigger.trigger_id,
        occurrence_key="go-1",
        start_workflow=start,
        now=NOW,
    )
    started = require_workflow_run(conn, firing.workflow_id)
    # The best-effort wake must run decide_once on the new run, not leave it
    # idle at revision 0 until a recovery scan.
    assert started.revision >= 1
    assert started.status == WorkflowStatus.WAITING


def test_routing_teams_required_session_and_admission_outcomes() -> None:
    conn = _conn()
    _, activity_id = _create_activity(conn)
    activity = get_activity(conn, activity_id)
    assert activity is not None
    required = activity.requirements.model_copy(
        update={
            "preferred_models": ("team",),
            "session_strategy": "require_existing",
        }
    )
    candidate = RouteCandidate(
        harness="codex",
        models=("gpt-5", "gpt-5-review"),
        capability_tags=frozenset({"coding"}),
        structured_protocol=True,
    )
    held = decide_route(
        RoutingContext(
            activity_id=activity_id,
            requirements=required,
            candidates=(candidate,),
            model_teams={"team": ("gpt-5", "gpt-5-review")},
        )
    )
    assert held.action == "hold"
    candidate = candidate.model_copy(update={"reusable_session_ids": (uuid4(),)})
    routed_decision = decide_route(
        RoutingContext(
            activity_id=activity_id,
            requirements=required,
            candidates=(candidate,),
            model_teams={"team": ("gpt-5", "gpt-5-review")},
        )
    )
    assert routed_decision.route is not None
    assert [assignment.role for assignment in routed_decision.route.assignments] == [
        "primary",
        "reviewer",
    ]
    routed = persist_route(conn, activity_id, routed_decision.route, now=NOW)
    base = AdmissionContext(
        activity=routed,
        running_total=1,
        max_running=1,
        repository="repo",
        queued_at=NOW,
        now=NOW,
    )
    assert isinstance(decide_admission(base), Deferred)
    assert isinstance(
        decide_admission(base.model_copy(update={"activity": activity})),
        Rejected,
    )


def test_all_trigger_specs_validate_and_start_rollback_is_atomic() -> None:
    conn = _conn()
    specs = (
        CronTrigger(expression="0 * * * *"),
        FactTrigger(fact_kind="build.completed"),
        RepositoryTrigger(repository_id=uuid4(), paths=("src/**",)),
        ManualTrigger(command="run"),
    )
    for spec in specs:
        TriggerDefinition(
            trigger_id=uuid4(),
            name=f"trigger-{spec.type}",
            version=1,
            spec=spec,
            target=StartWorkflowTarget(
                definition_name="phase4",
                definition_version=1,
            ),
            created_at=NOW,
        )
    trigger = TriggerDefinition(
        trigger_id=uuid4(),
        name="rollback",
        version=1,
        spec=ManualTrigger(command="rollback"),
        target=StartWorkflowTarget(definition_name="phase4", definition_version=1),
        created_at=NOW,
    )
    create_trigger(conn, trigger)

    def failing_start(
        connection: sqlite3.Connection,
        target: StartWorkflowTarget,
        now: datetime,
    ) -> UUID:
        del target, now
        run = _run()
        create_workflow_run(
            connection,
            run,
            waits=(ExternalSignalWait(signal_name="never"),),
        )
        raise RuntimeError("injected start failure")

    with pytest.raises(RuntimeError, match="injected"):
        fire_trigger(
            conn,
            trigger.trigger_id,
            occurrence_key="one",
            start_workflow=failing_start,
            now=NOW,
        )
    assert conn.execute("SELECT COUNT(*) AS n FROM trigger_firings").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"] == 0


def test_dispatcher_recovers_expired_claim_and_executes() -> None:
    conn = _conn()
    _, activity_id = _create_activity(conn)
    _route_and_admit(conn, activity_id)
    claim_activity(
        conn,
        activity_id,
        owner="crashed-worker",
        lease_for=timedelta(seconds=5),
        capability_revision=7,
        now=NOW,
    )
    dispatch_now = NOW + timedelta(minutes=2)

    def router(activity: object) -> object:
        raise AssertionError("recovered routed activity must not reroute")

    def admission(activity: object) -> Admitted:
        assert hasattr(activity, "activity_id")
        decision = decide_admission(
            AdmissionContext(
                activity=activity,  # type: ignore[arg-type]
                running_total=0,
                max_running=1,
                repository="repo",
                queued_at=NOW,
                now=dispatch_now,
            )
        )
        assert isinstance(decision, Admitted)
        return decision

    async def executor(
        activity: object,
        claim: object,
        renew: object,
    ) -> ActivitySuccess:
        del activity, claim, renew
        return ActivitySuccess(output={"recovered": True})

    dispatcher = ActivityDispatcher(
        conn,
        router=router,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        worker_id="replacement",
        clock=lambda: dispatch_now,
    )
    report = asyncio.run(dispatcher.tick())
    assert report.reaped_claims == 1
    assert report.reaped_reservations == 1
    assert report.completed == 1
    assert get_activity(conn, activity_id).status == ActivityStatus.SUCCEEDED


def test_dispatcher_records_executor_exception_as_activity_failure() -> None:
    conn = _conn()
    _, activity_id = _create_activity(conn)
    conn.execute(
        "UPDATE activities SET max_attempts = 1 WHERE activity_id = ?",
        (str(activity_id),),
    )

    def router(activity: object) -> object:
        routing = decide_route(
            RoutingContext(
                activity_id=activity.activity_id,  # type: ignore[attr-defined]
                requirements=activity.requirements,  # type: ignore[attr-defined]
                candidates=(
                    RouteCandidate(
                        harness="codex",
                        models=("gpt-5",),
                        capability_tags=frozenset({"coding"}),
                        structured_protocol=True,
                        capability_revision=7,
                    ),
                ),
            )
        )
        assert routing.route is not None
        return routing.route

    def admission(activity: object) -> Admitted:
        decision = decide_admission(
            AdmissionContext(
                activity=activity,  # type: ignore[arg-type]
                running_total=0,
                max_running=1,
                repository="repo",
                queued_at=NOW,
                now=NOW,
            )
        )
        assert isinstance(decision, Admitted)
        return decision

    async def executor(activity: object, claim: object, renew: object) -> ActivitySuccess:
        del activity, claim, renew
        raise RuntimeError("executor blew up")

    dispatcher = ActivityDispatcher(
        conn,
        router=router,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        worker_id="worker",
        clock=lambda: NOW,
    )
    report = asyncio.run(dispatcher.tick())
    assert report.completed == 1
    activity = get_activity(conn, activity_id)
    assert activity is not None
    assert activity.status == ActivityStatus.FAILED
    row = conn.execute(
        "SELECT outcome_json FROM activity_results WHERE activity_id = ?",
        (str(activity_id),),
    ).fetchone()
    assert row is not None
    assert "executor_error" in str(row["outcome_json"])
    assert "executor blew up" in str(row["outcome_json"])


def test_dispatcher_defers_reservation_lock_conflict_without_wedging_tick() -> None:
    conn = _conn()
    _, first_id = _create_activity(conn)
    _, second_id = _create_activity(conn)
    # Ensure the unadmitted activity is processed first (would wedge the tick).
    conn.execute(
        "UPDATE activities SET priority = 10 WHERE activity_id = ?",
        (str(first_id),),
    )
    lock = "worktree:shared"
    for activity_id in (first_id, second_id):
        activity = get_activity(conn, activity_id)
        assert activity is not None
        routing = decide_route(
            RoutingContext(
                activity_id=activity_id,
                requirements=activity.requirements,
                candidates=(
                    RouteCandidate(
                        harness="codex",
                        models=("gpt-5",),
                        capability_tags=frozenset({"coding"}),
                        structured_protocol=True,
                        capability_revision=7,
                    ),
                ),
            )
        )
        assert routing.route is not None
        persist_route(conn, activity_id, routing.route, now=NOW)
    # Second holds the reservation lock but remains unclaimed.
    second = get_activity(conn, second_id)
    assert second is not None
    decision = decide_admission(
        AdmissionContext(
            activity=second,
            running_total=0,
            max_running=4,
            repository="repo",
            queued_at=NOW,
            now=NOW,
            required_locks=frozenset({lock}),
        )
    )
    assert isinstance(decision, Admitted)
    persist_admission(conn, second_id, decision, now=NOW)

    def router(activity: object) -> object:
        raise AssertionError("both activities are already routed")

    def admission(activity: object) -> Admitted:
        result = decide_admission(
            AdmissionContext(
                activity=activity,  # type: ignore[arg-type]
                running_total=0,
                max_running=4,
                repository="repo",
                queued_at=NOW,
                now=NOW,
                required_locks=frozenset({lock}),
            )
        )
        assert isinstance(result, Admitted)
        return result

    async def executor(activity: object, claim: object, renew: object) -> ActivitySuccess:
        del claim, renew
        return ActivitySuccess(output={"id": str(activity.activity_id)})  # type: ignore[attr-defined]

    dispatcher = ActivityDispatcher(
        conn,
        router=router,  # type: ignore[arg-type]
        admission=admission,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        worker_id="worker",
        clock=lambda: NOW,
    )
    report = asyncio.run(dispatcher.tick())
    assert report.deferred >= 1
    assert report.completed == 1
    first = get_activity(conn, first_id)
    second = get_activity(conn, second_id)
    assert first is not None and second is not None
    assert first.status == ActivityStatus.WAITING_ADMISSION
    assert second.status == ActivityStatus.SUCCEEDED


def test_trigger_dispatcher_polls_all_sources_and_persists_cursors() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="occurrence"),),
    )
    specs = (
        CronTrigger(expression="* * * * *"),
        FactTrigger(fact_kind="fact"),
        RepositoryTrigger(repository_id=uuid4()),
        ManualTrigger(command="run"),
    )
    for spec in specs:
        create_trigger(
            conn,
            TriggerDefinition(
                trigger_id=uuid4(),
                name=spec.type,
                version=1,
                spec=spec,
                target=SignalWorkflowTarget(
                    workflow_id=run.workflow_id,
                    signal_name="occurrence",
                ),
                created_at=NOW,
            ),
        )

    class Occurrences:
        def cron(self, trigger, spec, cursor, now):  # type: ignore[no-untyped-def]
            del spec, cursor, now
            return (f"{trigger.name}:1",)

        def facts(self, trigger, spec, cursor, now):  # type: ignore[no-untyped-def]
            del spec, cursor, now
            return (f"{trigger.name}:1",)

        def repository(self, trigger, spec, cursor, now):  # type: ignore[no-untyped-def]
            del spec, cursor, now
            return (f"{trigger.name}:1",)

        def manual(self, trigger, spec, cursor, now):  # type: ignore[no-untyped-def]
            del spec, cursor, now
            return (f"{trigger.name}:1",)

    def no_start(connection, target, now):  # type: ignore[no-untyped-def]
        del connection, target, now
        raise AssertionError("signal triggers must not start workflows")

    dispatcher = TriggerDispatcher(
        conn,
        occurrences=Occurrences(),
        start_workflow=no_start,
        clock=lambda: NOW,
    )
    assert dispatcher.tick() == TRIGGER_SPEC_COUNT
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM trigger_firings").fetchone()["n"]
        == TRIGGER_SPEC_COUNT
    )
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM trigger_cursors").fetchone()["n"]
        == TRIGGER_SPEC_COUNT
    )


def test_default_fact_trigger_observes_retained_facts_and_resumes() -> None:
    conn = _conn()
    run = _run()
    create_workflow_run(
        conn,
        run,
        waits=(ExternalSignalWait(signal_name="occurrence"),),
    )
    trigger_id = uuid4()
    create_trigger(
        conn,
        TriggerDefinition(
            trigger_id=trigger_id,
            name="successful build",
            version=1,
            spec=FactTrigger(
                fact_kind="build.completed",
                predicate={"result": {"status": "ok"}},
            ),
            target=SignalWorkflowTarget(
                workflow_id=run.workflow_id,
                signal_name="occurrence",
            ),
            created_at=NOW,
        ),
    )
    append_fact(
        conn,
        RetainedFactDraft(
            kind="build.completed",
            occurred_at=NOW,
            actor=FactActor(kind="service", id="builder"),
            correlation=FactCorrelation(correlation_id=uuid4()),
            payload={"result": {"status": "ok"}},
        ),
        recorded_at=NOW,
    )

    dispatcher = build_default_trigger_dispatcher(conn)
    assert dispatcher.tick() == 1
    assert dispatcher.tick() == 0
    assert conn.execute(
        "SELECT cursor FROM trigger_cursors WHERE trigger_id = ?",
        (str(trigger_id),),
    ).fetchone()["cursor"] == "2"


def test_trigger_loop_continues_after_transient_tick_failure() -> None:
    class FlakyTriggerDispatcher:
        calls = 0

        def tick(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            runtime._shutdown.set()
            return 0

    runtime = object.__new__(Runtime)
    runtime._shutdown = asyncio.Event()
    dispatcher = FlakyTriggerDispatcher()
    runtime.trigger_dispatcher = dispatcher

    asyncio.run(runtime._phase4_trigger_loop())

    assert dispatcher.calls == EXPECTED_TRANSIENT_TICKS


def test_cron_trigger_fires_when_due() -> None:
    from murder.runtime import trigger_dispatcher as td

    conn = _conn()
    trigger_id = uuid4()
    create_trigger(
        conn,
        TriggerDefinition(
            trigger_id=trigger_id,
            name="hourly",
            version=1,
            spec=CronTrigger(expression="0 * * * *", timezone="UTC"),
            target=StartWorkflowTarget(definition_name="phase4", definition_version=1),
            created_at=NOW,
        ),
    )
    clock = {"now": NOW}
    dispatcher = TriggerDispatcher(
        conn,
        occurrences=td.DurableTriggerOccurrences(conn),
        start_workflow=td._start_trigger_workflow,
        clock=lambda: clock["now"],
    )
    assert dispatcher.tick() == 0  # seed cursor, no backlog
    assert conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"] == 0
    clock["now"] = NOW + timedelta(hours=1)
    assert dispatcher.tick() == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"] == 1
    assert dispatcher.tick() == 0
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM trigger_firings WHERE trigger_id = ?",
        (str(trigger_id),),
    ).fetchone()["n"] == 1


def test_manual_enqueue_tick_starts_workflow() -> None:
    from murder.app.service.handlers import trigger as trigger_handler
    from murder.runtime import trigger_dispatcher as td
    from murder.state.persistence.triggers import enqueue_manual_trigger_fire

    conn = _conn()
    trigger_id = uuid4()
    create_trigger(
        conn,
        TriggerDefinition(
            trigger_id=trigger_id,
            name="manual-run",
            version=1,
            spec=ManualTrigger(command="run"),
            target=StartWorkflowTarget(definition_name="phase4", definition_version=1),
            created_at=NOW,
        ),
    )
    key = enqueue_manual_trigger_fire(
        conn,
        trigger_id,
        occurrence_key="click-42",
        now=NOW,
    )
    assert key == "click-42"

    class _Host:
        runtime = type("R", (), {"db": conn})()

        def register_rpc_handler(self, method: str, handler: object) -> None:
            del method
            self.handler = handler

    host = _Host()
    trigger_handler.register(host)  # type: ignore[arg-type]
    duplicate = host.handler({"trigger_id": str(trigger_id), "occurrence_key": "click-42"})
    assert duplicate["ok"] is True
    assert duplicate["occurrence_key"] == "click-42"

    dispatcher = TriggerDispatcher(
        conn,
        occurrences=td.DurableTriggerOccurrences(conn),
        start_workflow=td._start_trigger_workflow,
        clock=lambda: NOW,
    )
    assert dispatcher.tick() == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"] == 1
    assert dispatcher.tick() == 0
    assert conn.execute(
        "SELECT occurrence_key FROM trigger_firings WHERE trigger_id = ?",
        (str(trigger_id),),
    ).fetchone()["occurrence_key"] == "click-42"


def test_repository_fingerprint_change_fires_after_debounce() -> None:
    from uuid import NAMESPACE_URL, uuid5

    from murder.runtime import trigger_dispatcher as td

    conn = _conn()
    repo_root = Path("/tmp/murder-repo-trigger-test")
    repository_id = uuid5(NAMESPACE_URL, f"murder:repository:{repo_root.resolve()}")
    fingerprints = ["aaa"]

    create_trigger(
        conn,
        TriggerDefinition(
            trigger_id=uuid4(),
            name="repo-change",
            version=1,
            spec=RepositoryTrigger(
                repository_id=repository_id,
                debounce_seconds=REPOSITORY_DEBOUNCE_SECONDS,
            ),
            target=StartWorkflowTarget(definition_name="phase4", definition_version=1),
            created_at=NOW,
        ),
    )

    def fingerprint(_root: Path) -> str:
        return fingerprints[0]

    clock = {"now": NOW}
    dispatcher = TriggerDispatcher(
        conn,
        occurrences=td.DurableTriggerOccurrences(
            conn,
            repo_root=repo_root,
            repository_fingerprint=fingerprint,
        ),
        start_workflow=td._start_trigger_workflow,
        clock=lambda: clock["now"],
    )
    assert dispatcher.tick() == 0  # seed baseline fingerprint
    fingerprints[0] = "bbb"
    assert dispatcher.tick() == 0  # change observed, debounce pending
    clock["now"] = NOW + timedelta(seconds=REPOSITORY_DEBOUNCE_SECONDS - 1)
    assert dispatcher.tick() == 0
    clock["now"] = NOW + timedelta(seconds=REPOSITORY_DEBOUNCE_SECONDS)
    assert dispatcher.tick() == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"] == 1
    assert dispatcher.tick() == 0


def test_default_activity_dispatcher_routes_admits_and_requires_session() -> None:
    from murder.runtime.activity_dispatcher import build_default_activity_dispatcher
    from murder.state.persistence.harness_models import upsert_harness_models

    conn = _conn()
    _, activity_id = _create_activity(conn)
    upsert_harness_models(
        conn,
        harness="codex",
        models=[{"id": "gpt-5", "label": "GPT-5"}],
    )
    dispatcher = build_default_activity_dispatcher(conn, worker_id="test-worker")
    report = asyncio.run(dispatcher.tick())
    activity = get_activity(conn, activity_id)
    assert activity is not None
    assert report.routed == 1
    assert report.admitted == 1
    assert report.completed == 1
    # No live session yet — executor fails retryably and returns to admission.
    assert activity.status == ActivityStatus.WAITING_ADMISSION
    row = conn.execute(
        "SELECT outcome_json FROM activity_results WHERE activity_id = ?",
        (str(activity_id),),
    ).fetchone()
    assert row is not None
    assert "session_required" in str(row["outcome_json"])
