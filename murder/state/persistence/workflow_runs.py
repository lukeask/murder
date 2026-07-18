"""Authoritative persistence for workflow state, waits, and addressed signals.

The DAO stores only the current typed state document.  It never serializes
Python execution state and never reconstructs a run by replaying code or ticket
history.  ``apply_transition_plan`` is the sole state-transition write path and
uses optimistic concurrency inside one SQLite transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4, uuid5

from pydantic import JsonValue, TypeAdapter

from murder.facts.contracts import (
    AggregateRef as FactAggregateRef,
)
from murder.facts.contracts import (
    FactActor,
    FactCorrelation,
    ProjectionInputDraft,
    RetainedFactDraft,
    WorkflowStartedPayload,
    WorkflowStateMigratedPayload,
    WorkflowTransitionAppliedPayload,
)
from murder.facts.log import append_fact
from murder.work.workflows.runtime import (
    ActivityFinishedSignal,
    ActivityWait,
    ApprovalResolvedSignal,
    ApprovalWait,
    ExternalSignalWait,
    ExternalWorkflowSignal,
    JoinWait,
    TimerFiredSignal,
    TimerWait,
    VersionedState,
    WaitSpec,
    WorkflowContract,
    WorkflowDecisionInput,
    WorkflowRunRecord,
    WorkflowSignalPayload,
    WorkflowSignalRecord,
    WorkflowStateMigrationRecord,
    WorkflowStatus,
    WorkflowTransitionPlan,
    WorkflowWaitRecord,
)

_WAIT_ADAPTER: TypeAdapter[WaitSpec] = TypeAdapter(WaitSpec)
_SIGNAL_ADAPTER: TypeAdapter[WorkflowSignalPayload] = TypeAdapter(WorkflowSignalPayload)
_WORKFLOW_FACT_NAMESPACE = UUID("6a3761aa-a34f-5d83-8ac4-1d4d59290c4f")


class WorkflowPersistenceError(RuntimeError):
    """Base class for workflow persistence invariant failures."""


class WorkflowNotFoundError(WorkflowPersistenceError):
    pass


class StaleWorkflowRevisionError(WorkflowPersistenceError):
    def __init__(self, workflow_id: UUID, expected: int, actual: int) -> None:
        super().__init__(f"workflow {workflow_id} revision is {actual}, expected {expected}")
        self.workflow_id = workflow_id
        self.expected = expected
        self.actual = actual


class SignalDeduplicationConflictError(WorkflowPersistenceError):
    pass


class SignalConsumptionError(WorkflowPersistenceError):
    pass


class WorkflowStateMigrationRequiredError(WorkflowPersistenceError):
    pass


class TerminalWorkflowTransitionError(WorkflowPersistenceError):
    pass


def create_workflow_run(
    conn: sqlite3.Connection,
    run: WorkflowRunRecord,
    *,
    waits: Sequence[WaitSpec] = (),
) -> None:
    """Insert an authoritative run and its initial waits atomically."""

    if run.status == WorkflowStatus.WAITING and not waits:
        raise ValueError("a waiting workflow requires at least one explicit wait")
    if (
        run.status
        in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
        and waits
    ):
        raise ValueError("a terminal workflow cannot have outstanding waits")

    with _transaction(conn, "create_workflow"):
        conn.execute(
            """
            INSERT INTO workflow_runs(
                workflow_id, definition_name, definition_version, status,
                revision, state_json, created_at, updated_at, started_by_json,
                correlation_json, terminal_reason, parent_ticket_id,
                definition_json, stage_map_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.workflow_id),
                run.definition_name,
                run.definition_version,
                run.status.value,
                run.revision,
                _json(run.state.model_dump(mode="json")),
                _datetime_text(run.created_at),
                _datetime_text(run.updated_at),
                _json(run.started_by.model_dump(mode="json")),
                _json(run.correlation.model_dump(mode="json")),
                run.terminal_reason,
                run.parent_ticket_id,
                _json(run.definition_snapshot) if run.definition_snapshot is not None else None,
                _json(run.stage_map),
            ),
        )
        _insert_waits(
            conn,
            workflow_id=run.workflow_id,
            waits=waits,
            created_at=run.created_at,
        )
        started_payload = WorkflowStartedPayload(
            workflow_id=run.workflow_id,
            definition_name=run.definition_name,
            definition_version=run.definition_version,
            status=run.status.value,
        )
        _append_workflow_fact(
            conn,
            workflow=run,
            revision=run.revision,
            fact_id=uuid5(
                _WORKFLOW_FACT_NAMESPACE,
                f"{run.workflow_id}:{run.revision}:started",
            ),
            kind=started_payload.type,
            payload=started_payload.model_dump(mode="json"),
            occurred_at=run.created_at,
            invalidate=True,
        )


def get_workflow_run(
    conn: sqlite3.Connection, workflow_or_parent_id: UUID | str
) -> WorkflowRunRecord | None:
    """Load by UUID identity, or by legacy parent ticket id during migration."""

    key = str(workflow_or_parent_id)
    row = conn.execute(
        """
        SELECT * FROM workflow_runs
        WHERE workflow_id = ? OR parent_ticket_id = ?
        LIMIT 1
        """,
        (key, key),
    ).fetchone()
    return _run_record(row) if row is not None else None


def require_workflow_run(conn: sqlite3.Connection, workflow_id: UUID) -> WorkflowRunRecord:
    run = get_workflow_run(conn, workflow_id)
    if run is None:
        raise WorkflowNotFoundError(f"workflow {workflow_id} does not exist")
    return run


def list_workflow_runs(conn: sqlite3.Connection) -> list[WorkflowRunRecord]:
    rows = conn.execute("SELECT * FROM workflow_runs ORDER BY created_at, workflow_id").fetchall()
    return [_run_record(row) for row in rows]


def list_workflow_waits(
    conn: sqlite3.Connection,
    workflow_id: UUID,
    *,
    include_satisfied: bool = True,
) -> list[WorkflowWaitRecord]:
    predicate = "" if include_satisfied else " AND satisfied_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT * FROM workflow_waits
        WHERE workflow_id = ?{predicate}
        ORDER BY created_at, wait_id
        """,
        (str(workflow_id),),
    ).fetchall()
    return [_wait_record(row) for row in rows]


def enqueue_workflow_signal(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    deduplication_key: str,
    payload: WorkflowSignalPayload,
    created_at: datetime | None = None,
    signal_id: UUID | None = None,
) -> WorkflowSignalRecord:
    """Create an addressed inbox record idempotently.

    Reusing a deduplication key with the identical payload returns the original
    record.  Reusing it for different data is rejected instead of silently
    discarding one producer's signal.
    """

    if not deduplication_key:
        raise ValueError("deduplication_key must not be empty")
    timestamp = _aware(created_at)
    candidate_id = signal_id or uuid4()
    payload_json = _json(_SIGNAL_ADAPTER.dump_python(payload, mode="json"))

    with _transaction(conn, "enqueue_workflow_signal"):
        if get_workflow_run(conn, workflow_id) is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id} does not exist")
        existing = conn.execute(
            """
            SELECT * FROM workflow_signals
            WHERE workflow_id = ? AND deduplication_key = ?
            """,
            (str(workflow_id), deduplication_key),
        ).fetchone()
        if existing is not None:
            record = _signal_record(existing)
            if record.payload != payload:
                raise SignalDeduplicationConflictError(
                    f"deduplication key {deduplication_key!r} already has a different payload"
                )
            return record

        conn.execute(
            """
            INSERT INTO workflow_signals(
                signal_id, workflow_id, deduplication_key, created_at,
                payload_json, consumed_at, consumed_at_revision
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                str(candidate_id),
                str(workflow_id),
                deduplication_key,
                _datetime_text(timestamp),
                payload_json,
            ),
        )

        # A signal marks matching current waits satisfied, but does not consume
        # itself and does not imply that a state transition occurred.
        for wait in list_workflow_waits(conn, workflow_id, include_satisfied=False):
            if _signal_satisfies_wait(
                conn,
                workflow_id=workflow_id,
                payload=payload,
                wait=wait.spec,
            ):
                conn.execute(
                    """
                    UPDATE workflow_waits
                    SET satisfied_at = ?, satisfied_by_signal_id = ?
                    WHERE wait_id = ? AND satisfied_at IS NULL
                    """,
                    (
                        _datetime_text(timestamp),
                        str(candidate_id),
                        str(wait.wait_id),
                    ),
                )

    created_record = get_workflow_signal(conn, candidate_id)
    assert created_record is not None
    return created_record


def get_workflow_signal(conn: sqlite3.Connection, signal_id: UUID) -> WorkflowSignalRecord | None:
    row = conn.execute(
        "SELECT * FROM workflow_signals WHERE signal_id = ?",
        (str(signal_id),),
    ).fetchone()
    return _signal_record(row) if row is not None else None


def list_workflow_signals(
    conn: sqlite3.Connection,
    workflow_id: UUID,
    *,
    include_consumed: bool = False,
    limit: int = 100,
) -> list[WorkflowSignalRecord]:
    if limit < 1:
        raise ValueError("limit must be positive")
    predicate = "" if include_consumed else " AND consumed_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT * FROM workflow_signals
        WHERE workflow_id = ?{predicate}
        ORDER BY created_at, signal_id
        LIMIT ?
        """,
        (str(workflow_id), limit),
    ).fetchall()
    return [_signal_record(row) for row in rows]


def load_workflow_decision_input(
    conn: sqlite3.Connection,
    workflow_id: UUID,
    *,
    now: datetime | None = None,
    signal_limit: int = 100,
) -> WorkflowDecisionInput:
    """Load one finite, current decision batch without replay."""

    return WorkflowDecisionInput(
        run=require_workflow_run(conn, workflow_id),
        waits=tuple(list_workflow_waits(conn, workflow_id)),
        signals=tuple(
            list_workflow_signals(
                conn,
                workflow_id,
                include_consumed=False,
                limit=signal_limit,
            )
        ),
        now=_aware(now),
    )


def apply_transition_plan(  # noqa: PLR0912 - closed transactional invariants
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    plan: WorkflowTransitionPlan,
    applied_at: datetime | None = None,
) -> WorkflowRunRecord:
    """Atomically apply one pure workflow decision.

    The revision check, selected signal consumption, state replacement, wait
    replacement, and transition outbox writes either all commit or all roll
    back.  A stale caller must reload and decide again.
    """

    if len(set(plan.consume_signal_ids)) != len(plan.consume_signal_ids):
        raise SignalConsumptionError("consume_signal_ids contains duplicates")
    if plan.state.status == WorkflowStatus.WAITING and not plan.replace_waits:
        raise ValueError("a waiting transition requires at least one explicit wait")
    if (
        plan.state.status
        in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
        and plan.replace_waits
    ):
        raise ValueError("a terminal transition cannot install waits")

    timestamp = _aware(applied_at)
    new_revision = plan.state.expected_revision + 1
    with _transaction(conn, "apply_workflow_transition", immediate=True):
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE workflow_id = ?",
            (str(workflow_id),),
        ).fetchone()
        if row is None:
            raise WorkflowNotFoundError(f"workflow {workflow_id} does not exist")
        actual_revision = int(row["revision"])
        if actual_revision != plan.state.expected_revision:
            raise StaleWorkflowRevisionError(
                workflow_id,
                plan.state.expected_revision,
                actual_revision,
            )
        if WorkflowStatus(str(row["status"])) in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }:
            raise TerminalWorkflowTransitionError(
                f"terminal workflow {workflow_id} cannot transition"
            )
        current_state = VersionedState.model_validate(json.loads(str(row["state_json"])))
        if (
            current_state.schema_name,
            current_state.schema_version,
        ) != (
            plan.state.state.schema_name,
            plan.state.state.schema_version,
        ):
            raise WorkflowStateMigrationRequiredError(
                "workflow state schema changes require apply_workflow_state_migration"
            )

        if plan.consume_signal_ids:
            placeholders = ",".join("?" for _ in plan.consume_signal_ids)
            signal_rows = conn.execute(
                f"""
                SELECT signal_id, workflow_id, consumed_at
                FROM workflow_signals
                WHERE signal_id IN ({placeholders})
                """,
                tuple(str(signal_id) for signal_id in plan.consume_signal_ids),
            ).fetchall()
            if len(signal_rows) != len(plan.consume_signal_ids):
                raise SignalConsumptionError("one or more selected signals do not exist")
            for signal_row in signal_rows:
                if str(signal_row["workflow_id"]) != str(workflow_id):
                    raise SignalConsumptionError(
                        "a workflow may only consume its own addressed signals"
                    )
                if signal_row["consumed_at"] is not None:
                    raise SignalConsumptionError("a selected signal is already consumed")

            conn.execute(
                f"""
                UPDATE workflow_signals
                SET consumed_at = ?, consumed_at_revision = ?
                WHERE signal_id IN ({placeholders})
                """,
                (
                    _datetime_text(timestamp),
                    new_revision,
                    *(str(signal_id) for signal_id in plan.consume_signal_ids),
                ),
            )

        updated = conn.execute(
            """
            UPDATE workflow_runs
            SET status = ?, revision = ?, state_json = ?, updated_at = ?,
                terminal_reason = ?
            WHERE workflow_id = ? AND revision = ?
            """,
            (
                plan.state.status.value,
                new_revision,
                _json(plan.state.state.model_dump(mode="json")),
                _datetime_text(timestamp),
                plan.state.terminal_reason,
                str(workflow_id),
                plan.state.expected_revision,
            ),
        )
        if updated.rowcount != 1:
            # Defensive even though BEGIN IMMEDIATE serializes other writers.
            current = require_workflow_run(conn, workflow_id)
            raise StaleWorkflowRevisionError(
                workflow_id,
                plan.state.expected_revision,
                current.revision,
            )

        # The old decision set (including briefly satisfied waits) is replaced
        # as a unit; no absent-task or in-memory-future semantics remain.
        conn.execute(
            "DELETE FROM workflow_waits WHERE workflow_id = ?",
            (str(workflow_id),),
        )
        _insert_waits(
            conn,
            workflow_id=workflow_id,
            waits=plan.replace_waits,
            created_at=timestamp,
        )
        if plan.activities:
            from murder.state.persistence.activities import (  # noqa: PLC0415
                insert_activity_requests,
            )

            insert_activity_requests(
                conn,
                workflow_id=workflow_id,
                workflow_revision=new_revision,
                drafts=plan.activities,
                created_at=timestamp,
            )
        _validate_activity_wait_references(
            conn,
            workflow_id=workflow_id,
            waits=plan.replace_waits,
        )
        if plan.approvals:
            from murder.state.persistence.approvals import (  # noqa: PLC0415
                insert_approval_requests,
            )

            insert_approval_requests(
                conn,
                workflow_id=workflow_id,
                workflow_revision=new_revision,
                drafts=plan.approvals,
                created_at=timestamp,
            )
        _append_transition_outbox(
            conn,
            workflow_id=workflow_id,
            revision=new_revision,
            plan=plan,
            created_at=timestamp,
        )
        _append_transition_facts(
            conn,
            workflow=_run_record(row),
            revision=new_revision,
            plan=plan,
            created_at=timestamp,
        )

    return require_workflow_run(conn, workflow_id)


def apply_workflow_state_migration(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    expected_revision: int,
    target_state: VersionedState,
    migration_name: str,
    migrated_at: datetime | None = None,
) -> WorkflowStateMigrationRecord:
    """Replace a state schema explicitly and record the compatibility boundary."""

    if not migration_name:
        raise ValueError("migration_name must not be empty")
    timestamp = _aware(migrated_at)
    migration_id = uuid4()
    with _transaction(conn, "migrate_workflow_state", immediate=True):
        current = require_workflow_run(conn, workflow_id)
        if current.revision != expected_revision:
            raise StaleWorkflowRevisionError(
                workflow_id,
                expected_revision,
                current.revision,
            )
        if (
            current.state.schema_name,
            current.state.schema_version,
        ) == (
            target_state.schema_name,
            target_state.schema_version,
        ):
            raise ValueError("state migration must change the state schema identity")
        to_revision = expected_revision + 1
        updated = conn.execute(
            """
            UPDATE workflow_runs
            SET revision = ?, state_json = ?, updated_at = ?
            WHERE workflow_id = ? AND revision = ?
            """,
            (
                to_revision,
                _json(target_state.model_dump(mode="json")),
                _datetime_text(timestamp),
                str(workflow_id),
                expected_revision,
            ),
        )
        if updated.rowcount != 1:
            actual = require_workflow_run(conn, workflow_id).revision
            raise StaleWorkflowRevisionError(workflow_id, expected_revision, actual)
        conn.execute(
            """
            INSERT INTO workflow_state_migrations(
                migration_id, workflow_id, migration_name,
                from_schema_name, from_schema_version,
                to_schema_name, to_schema_version,
                from_revision, to_revision, migrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(migration_id),
                str(workflow_id),
                migration_name,
                current.state.schema_name,
                current.state.schema_version,
                target_state.schema_name,
                target_state.schema_version,
                expected_revision,
                to_revision,
                _datetime_text(timestamp),
            ),
        )
        migrated_payload = WorkflowStateMigratedPayload(
            workflow_id=workflow_id,
            migration_name=migration_name,
            from_schema_name=current.state.schema_name,
            from_schema_version=current.state.schema_version,
            to_schema_name=target_state.schema_name,
            to_schema_version=target_state.schema_version,
            revision=to_revision,
        )
        _append_workflow_fact(
            conn,
            workflow=current,
            revision=to_revision,
            fact_id=uuid5(
                _WORKFLOW_FACT_NAMESPACE,
                f"{workflow_id}:{to_revision}:state-migrated",
            ),
            kind=migrated_payload.type,
            payload=migrated_payload.model_dump(mode="json"),
            occurred_at=timestamp,
            invalidate=True,
        )
    return WorkflowStateMigrationRecord(
        migration_id=migration_id,
        workflow_id=workflow_id,
        migration_name=migration_name,
        from_schema_name=current.state.schema_name,
        from_schema_version=current.state.schema_version,
        to_schema_name=target_state.schema_name,
        to_schema_version=target_state.schema_version,
        from_revision=expected_revision,
        to_revision=to_revision,
        migrated_at=timestamp,
    )


def list_workflow_state_migrations(
    conn: sqlite3.Connection,
    workflow_id: UUID,
) -> tuple[WorkflowStateMigrationRecord, ...]:
    rows = conn.execute(
        """
        SELECT * FROM workflow_state_migrations
        WHERE workflow_id = ?
        ORDER BY to_revision
        """,
        (str(workflow_id),),
    ).fetchall()
    return tuple(WorkflowStateMigrationRecord.model_validate(dict(row)) for row in rows)


def _append_transition_outbox(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    revision: int,
    plan: WorkflowTransitionPlan,
    created_at: datetime,
) -> None:
    # Feature tables are now authoritative for activities and approvals, while
    # facts append directly to the retained fact log. Keep the compatibility
    # table readable, but create no second dispatch surface.
    batches: tuple[tuple[str, Sequence[WorkflowContract]], ...] = ()
    for kind, drafts in batches:
        for ordinal, draft in enumerate(drafts):
            conn.execute(
                """
                INSERT INTO workflow_transition_outbox(
                    outbox_id, workflow_id, workflow_revision, kind, ordinal,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(workflow_id),
                    revision,
                    kind,
                    ordinal,
                    _json(draft.model_dump(mode="json")),
                    _datetime_text(created_at),
                ),
            )


def _append_transition_facts(
    conn: sqlite3.Connection,
    *,
    workflow: WorkflowRunRecord,
    revision: int,
    plan: WorkflowTransitionPlan,
    created_at: datetime,
) -> None:
    """Append workflow facts with run provenance inside the state transaction."""

    workflow_aggregate = FactAggregateRef(
        kind="workflow",
        id=workflow.workflow_id,
        revision=revision,
    )
    transition_payload = WorkflowTransitionAppliedPayload(
        workflow_id=workflow.workflow_id,
        from_status=workflow.status.value,
        to_status=plan.state.status.value,
        revision=revision,
    )
    _append_workflow_fact(
        conn,
        workflow=workflow,
        revision=revision,
        fact_id=uuid5(
            _WORKFLOW_FACT_NAMESPACE,
            f"{workflow.workflow_id}:{revision}:transition",
        ),
        kind=transition_payload.type,
        payload=transition_payload.model_dump(mode="json"),
        occurred_at=created_at,
        invalidate=True,
    )

    for ordinal, draft in enumerate(plan.facts):
        aggregate = (
            FactAggregateRef(
                kind=draft.aggregate.kind,
                id=draft.aggregate.id,
                revision=draft.aggregate.revision,
            )
            if draft.aggregate is not None
            else workflow_aggregate
        )
        _append_workflow_fact(
            conn,
            workflow=workflow,
            revision=revision,
            fact_id=uuid5(
                _WORKFLOW_FACT_NAMESPACE,
                f"{workflow.workflow_id}:{revision}:{ordinal}",
            ),
            kind=draft.kind,
            payload=draft.payload,
            occurred_at=created_at,
            aggregate=aggregate,
        )


def _append_workflow_fact(
    conn: sqlite3.Connection,
    *,
    workflow: WorkflowRunRecord,
    revision: int,
    fact_id: UUID,
    kind: str,
    payload: dict[str, JsonValue],
    occurred_at: datetime,
    aggregate: FactAggregateRef | None = None,
    invalidate: bool = False,
) -> None:
    append_fact(
        conn,
        RetainedFactDraft(
            fact_id=fact_id,
            kind=kind,
            occurred_at=occurred_at,
            aggregate=aggregate
            or FactAggregateRef(
                kind="workflow",
                id=workflow.workflow_id,
                revision=revision,
            ),
            actor=FactActor(
                kind=workflow.started_by.kind.value,
                id=workflow.started_by.id,
            ),
            correlation=FactCorrelation(
                correlation_id=workflow.correlation.correlation_id,
                causation_id=workflow.correlation.causation_id,
                trace_id=workflow.correlation.trace_id,
            ),
            payload=payload,
        ),
        projection_inputs=(
            (
                ProjectionInputDraft(
                    projection="workflow_runs",
                    subject_key=str(workflow.workflow_id),
                    generation=revision,
                ),
            )
            if invalidate
            else ()
        ),
        recorded_at=occurred_at,
    )


def _insert_waits(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    waits: Sequence[WaitSpec],
    created_at: datetime,
) -> None:
    for spec in waits:
        conn.execute(
            """
            INSERT INTO workflow_waits(
                wait_id, workflow_id, created_at, spec_json,
                satisfied_at, satisfied_by_signal_id
            ) VALUES (?, ?, ?, ?, NULL, NULL)
            """,
            (
                str(uuid4()),
                str(workflow_id),
                _datetime_text(created_at),
                _json(_WAIT_ADAPTER.dump_python(spec, mode="json")),
            ),
        )


def _validate_activity_wait_references(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    waits: Sequence[WaitSpec],
) -> None:
    referenced: set[UUID] = set()
    for wait in waits:
        if isinstance(wait, ActivityWait):
            referenced.add(wait.activity_id)
        elif isinstance(wait, JoinWait):
            referenced.update(wait.activity_ids)
    for activity_id in referenced:
        row = conn.execute(
            "SELECT workflow_id FROM activities WHERE activity_id = ?",
            (str(activity_id),),
        ).fetchone()
        if row is None or str(row["workflow_id"]) != str(workflow_id):
            raise ValueError(
                f"activity wait references unknown activity {activity_id} for this workflow"
            )


def _signal_satisfies_wait(  # noqa: PLR0911 - one return per closed wait variant
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    payload: WorkflowSignalPayload,
    wait: WaitSpec,
) -> bool:
    if isinstance(wait, ActivityWait) and isinstance(payload, ActivityFinishedSignal):
        return wait.activity_id == payload.activity_id
    if isinstance(wait, ApprovalWait) and isinstance(payload, ApprovalResolvedSignal):
        return wait.approval_id == payload.approval_id
    if isinstance(wait, TimerWait) and isinstance(payload, TimerFiredSignal):
        return wait.timer_id == payload.timer_id
    if isinstance(wait, ExternalSignalWait) and isinstance(payload, ExternalWorkflowSignal):
        return wait.signal_name == payload.name and (
            wait.correlation_key is None or wait.correlation_key == payload.correlation_key
        )
    if isinstance(wait, JoinWait) and isinstance(payload, ActivityFinishedSignal):
        if payload.activity_id not in wait.activity_ids:
            return False
        rows = conn.execute(
            """
            SELECT payload_json
            FROM workflow_signals
            WHERE workflow_id = ?
            """,
            (str(workflow_id),),
        ).fetchall()
        finished_ids: set[UUID] = set()
        for row in rows:
            candidate = _SIGNAL_ADAPTER.validate_python(json.loads(str(row["payload_json"])))
            if isinstance(candidate, ActivityFinishedSignal):
                finished_ids.add(candidate.activity_id)
        matched = len(finished_ids.intersection(wait.activity_ids))
        if wait.mode == "any":
            return matched >= 1
        if wait.mode == "all":
            return matched == len(wait.activity_ids)
        assert wait.threshold is not None
        return matched >= wait.threshold
    return False


def _run_record(row: sqlite3.Row) -> WorkflowRunRecord:
    definition_json = row["definition_json"]
    return WorkflowRunRecord.model_validate(
        {
            "workflow_id": row["workflow_id"],
            "definition_name": row["definition_name"],
            "definition_version": row["definition_version"],
            "status": row["status"],
            "revision": row["revision"],
            "state": json.loads(str(row["state_json"])),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_by": json.loads(str(row["started_by_json"])),
            "correlation": json.loads(str(row["correlation_json"])),
            "terminal_reason": row["terminal_reason"],
            "parent_ticket_id": row["parent_ticket_id"],
            "definition_snapshot": (
                json.loads(str(definition_json)) if definition_json is not None else None
            ),
            "stage_map": json.loads(str(row["stage_map_json"] or "{}")),
        }
    )


def _wait_record(row: sqlite3.Row) -> WorkflowWaitRecord:
    return WorkflowWaitRecord.model_validate(
        {
            "wait_id": row["wait_id"],
            "workflow_id": row["workflow_id"],
            "created_at": row["created_at"],
            "spec": json.loads(str(row["spec_json"])),
            "satisfied_at": row["satisfied_at"],
            "satisfied_by_signal_id": row["satisfied_by_signal_id"],
        }
    )


def _signal_record(row: sqlite3.Row) -> WorkflowSignalRecord:
    return WorkflowSignalRecord.model_validate(
        {
            "signal_id": row["signal_id"],
            "workflow_id": row["workflow_id"],
            "deduplication_key": row["deduplication_key"],
            "created_at": row["created_at"],
            "payload": json.loads(str(row["payload_json"])),
            "consumed_at": row["consumed_at"],
            "consumed_at_revision": row["consumed_at_revision"],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("workflow timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return _aware(value).isoformat(timespec="microseconds")


@contextmanager
def _transaction(
    conn: sqlite3.Connection,
    name: str,
    *,
    immediate: bool = False,
) -> Iterator[None]:
    """Transaction that remains atomic when called inside a larger transaction."""

    if conn.in_transaction:
        savepoint = f"murder_{name}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
        return

    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
