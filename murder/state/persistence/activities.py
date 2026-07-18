"""Persistence and fenced lifecycle for durable workflow activities."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import TypeAdapter

from murder.facts.contracts import (
    ActivityStateChangedPayload,
    AggregateRef,
    FactActor,
    FactCorrelation,
    ProjectionInputDraft,
    RetainedFactDraft,
)
from murder.facts.log import append_fact
from murder.runtime.admission import AdmissionDecision, Admitted, Rejected
from murder.state.persistence.workflow_runs import (
    enqueue_workflow_signal,
    require_workflow_run,
)
from murder.work.activities.runtime import (
    ActivityCancelled,
    ActivityClaim,
    ActivityFailure,
    ActivityOutcome,
    ActivityRecord,
    ActivityResultRecord,
    ActivityStatus,
    ActivitySuccess,
    ExecutionRoute,
)
from murder.work.workflows.runtime import (
    ActivityFinishedSignal,
    ActivityPayload,
    ActivityRequestDraft,
)

_PAYLOAD: TypeAdapter[ActivityPayload] = TypeAdapter(ActivityPayload)
_OUTCOME: TypeAdapter[ActivityOutcome] = TypeAdapter(ActivityOutcome)
_ACTIVITY_FACT_NAMESPACE = UUID("792f9708-f681-47b2-a53e-b3ef7a287c12")
LOGGER = logging.getLogger(__name__)


class ActivityLifecycleError(RuntimeError):
    pass


def insert_activity_requests(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    workflow_revision: int,
    drafts: Sequence[ActivityRequestDraft],
    created_at: datetime,
) -> None:
    """Insert transition-owned activities; caller owns the transition transaction."""

    for ordinal, draft in enumerate(drafts):
        conn.execute(
            """
            INSERT INTO activities(
                activity_id, workflow_id, workflow_revision, ordinal, status,
                revision,
                payload_json, requirements_json, idempotency_key, priority,
                retry_policy, max_attempts, route_json, route_id, session_id, attempts,
                claim_owner, claim_fence, claim_expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL,
                      0, NULL, 0, NULL, ?, ?)
            """,
            (
                str(draft.activity_id),
                str(workflow_id),
                workflow_revision,
                ordinal,
                _json(_PAYLOAD.dump_python(draft.payload, mode="json")),
                _json(draft.payload.requirements.model_dump(mode="json")),
                draft.idempotency_key,
                draft.priority,
                draft.retry_policy,
                draft.max_attempts,
                _time(created_at),
                _time(created_at),
            ),
        )
        _append_activity_input(
            conn,
            activity=_require(conn, draft.activity_id),
            operation="created",
            timestamp=created_at,
        )


def get_activity(conn: sqlite3.Connection, activity_id: UUID) -> ActivityRecord | None:
    row = conn.execute(
        "SELECT * FROM activities WHERE activity_id = ?",
        (str(activity_id),),
    ).fetchone()
    return _activity(row) if row is not None else None


def list_activities(
    conn: sqlite3.Connection,
    *,
    status: ActivityStatus | None = None,
) -> tuple[ActivityRecord, ...]:
    rows = conn.execute(
        "SELECT * FROM activities"
        + (" WHERE status = ?" if status is not None else "")
        + " ORDER BY priority DESC, created_at, activity_id",
        (() if status is None else (status.value,)),
    ).fetchall()
    return tuple(_activity(row) for row in rows)


def persist_route(
    conn: sqlite3.Connection,
    activity_id: UUID,
    route: ExecutionRoute,
    *,
    now: datetime | None = None,
) -> ActivityRecord:
    timestamp = _aware(now)
    with _transaction(conn, immediate=True):
        updated = conn.execute(
            """
            UPDATE activities
            SET status = 'waiting_admission', revision = revision + 1,
                route_json = ?, route_id = ?, session_id = ?, updated_at = ?
            WHERE activity_id = ? AND status IN ('pending', 'routing', 'waiting_admission')
            """,
            (
                _json(route.model_dump(mode="json")),
                str(route.route_id),
                str(route.selected_session_id) if route.selected_session_id else None,
                _time(timestamp),
                str(activity_id),
            ),
        )
        if updated.rowcount != 1:
            raise ActivityLifecycleError("activity cannot be routed from its current state")
        activity = _require(conn, activity_id)
        _append_activity_input(
            conn,
            activity=activity,
            operation="routed",
            timestamp=timestamp,
        )
    return activity


def persist_admission(
    conn: sqlite3.Connection,
    activity_id: UUID,
    decision: AdmissionDecision,
    *,
    now: datetime | None = None,
) -> ActivityRecord:
    timestamp = _aware(now)
    rejected_workflow_id: UUID | None = None
    with _transaction(conn, immediate=True):
        current = _require(conn, activity_id)
        if current.status != ActivityStatus.WAITING_ADMISSION:
            raise ActivityLifecycleError("only routed activities may be admitted")
        if isinstance(decision, Admitted):
            _release_expired_reservations(conn, timestamp)
            conn.execute(
                """
                INSERT INTO activity_reservations(
                    reservation_id, activity_id, reservation_keys_json,
                    admitted_at, expires_at, released_at
                ) VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(activity_id) DO UPDATE SET
                    reservation_id = excluded.reservation_id,
                    reservation_keys_json = excluded.reservation_keys_json,
                    admitted_at = excluded.admitted_at,
                    expires_at = excluded.expires_at,
                    released_at = NULL
                """,
                (
                    str(decision.reservation_id),
                    str(activity_id),
                    _json(list(decision.reservation_keys)),
                    _time(timestamp),
                    _time(decision.reservation_expires_at),
                ),
            )
            conn.execute(
                "DELETE FROM activity_reservation_locks WHERE activity_id = ?",
                (str(activity_id),),
            )
            try:
                conn.executemany(
                    """
                    INSERT INTO activity_reservation_locks(resource_key, activity_id, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        (
                            key,
                            str(activity_id),
                            _time(decision.reservation_expires_at),
                        )
                        for key in decision.reservation_keys
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ActivityLifecycleError("admission resource lock is already reserved") from exc
            conn.execute(
                """
                UPDATE activities
                   SET revision = revision + 1, updated_at = ?
                 WHERE activity_id = ?
                """,
                (_time(timestamp), str(activity_id)),
            )
            _append_activity_input(
                conn,
                activity=_require(conn, activity_id),
                operation="admitted",
                timestamp=timestamp,
            )
        elif isinstance(decision, Rejected):
            result_id = uuid4()
            outcome = ActivityCancelled(reason="; ".join(decision.reasons))
            conn.execute(
                """
                INSERT INTO activity_results(
                    result_id, activity_id, attempt, outcome_json, completed_at
                ) VALUES (?, ?, 0, ?, ?)
                """,
                (
                    str(result_id),
                    str(activity_id),
                    _json(_OUTCOME.dump_python(outcome, mode="json")),
                    _time(timestamp),
                ),
            )
            conn.execute(
                "UPDATE activities SET status = 'cancelled', "
                "revision = revision + 1, updated_at = ? "
                "WHERE activity_id = ?",
                (_time(timestamp), str(activity_id)),
            )
            _append_completion_fact(
                conn,
                activity=current,
                result_id=result_id,
                attempt=0,
                outcome=outcome,
                actor_kind="admission",
                actor_id="scheduler",
                timestamp=timestamp,
            )
            enqueue_workflow_signal(
                conn,
                workflow_id=current.workflow_id,
                deduplication_key=f"activity:{activity_id}:admission:rejected",
                payload=ActivityFinishedSignal(
                    activity_id=activity_id,
                    result_id=result_id,
                ),
                created_at=timestamp,
            )
            rejected_workflow_id = current.workflow_id
    if rejected_workflow_id is not None:
        _best_effort_wake(conn, rejected_workflow_id, timestamp)
    return _require(conn, activity_id)


def claim_activity(
    conn: sqlite3.Connection,
    activity_id: UUID,
    *,
    owner: str,
    lease_for: timedelta,
    capability_revision: int,
    now: datetime | None = None,
) -> ActivityClaim:
    if not owner or lease_for.total_seconds() <= 0:
        raise ValueError("claim owner and positive lease are required")
    timestamp = _aware(now)
    expires = timestamp + lease_for
    with _transaction(conn, immediate=True):
        row = conn.execute(
            """
            SELECT a.*, r.released_at, r.expires_at AS reservation_expires_at
            FROM activities AS a
            JOIN activity_reservations AS r ON r.activity_id = a.activity_id
            WHERE a.activity_id = ?
            """,
            (str(activity_id),),
        ).fetchone()
        if (
            row is None
            or row["released_at"] is not None
            or datetime.fromisoformat(str(row["reservation_expires_at"])) <= timestamp
        ):
            raise ActivityLifecycleError("activity has no active admission reservation")
        status = ActivityStatus(str(row["status"]))
        expiry = _parse_time(row["claim_expires_at"])
        if status not in {ActivityStatus.WAITING_ADMISSION, ActivityStatus.CLAIMED}:
            raise ActivityLifecycleError("activity is not claimable")
        if status == ActivityStatus.CLAIMED and expiry is not None and expiry > timestamp:
            raise ActivityLifecycleError("activity claim is still live")
        route = (
            ExecutionRoute.model_validate(json.loads(str(row["route_json"])))
            if row["route_json"]
            else None
        )
        if route is None or route.capability_revision != capability_revision:
            raise ActivityLifecycleError("activity route capability revision is stale")
        attempt = int(row["attempts"]) + 1
        fence = int(row["claim_fence"]) + 1
        conn.execute(
            """
            UPDATE activities SET status = 'claimed', revision = revision + 1,
                attempts = ?, claim_owner = ?,
                claim_fence = ?, claim_expires_at = ?, updated_at = ?
            WHERE activity_id = ?
            """,
            (attempt, owner, fence, _time(expires), _time(timestamp), str(activity_id)),
        )
        _append_activity_input(
            conn,
            activity=_require(conn, activity_id),
            operation="claimed",
            timestamp=timestamp,
        )
    return ActivityClaim(
        activity_id=activity_id,
        owner=owner,
        attempt=attempt,
        fence=fence,
        claimed_at=timestamp,
        expires_at=expires,
    )


def renew_activity_claim(
    conn: sqlite3.Connection,
    claim: ActivityClaim,
    *,
    lease_for: timedelta,
    now: datetime | None = None,
) -> ActivityClaim:
    timestamp = _aware(now)
    expires = timestamp + lease_for
    if lease_for.total_seconds() <= 0:
        raise ValueError("lease_for must be positive")
    with _transaction(conn, immediate=True):
        updated = conn.execute(
            """
            UPDATE activities SET revision = revision + 1,
                claim_expires_at = ?, updated_at = ?
            WHERE activity_id = ? AND status IN ('claimed','running')
              AND attempts = ? AND claim_owner = ? AND claim_fence = ?
              AND claim_expires_at > ?
            """,
            (
                _time(expires),
                _time(timestamp),
                str(claim.activity_id),
                claim.attempt,
                claim.owner,
                claim.fence,
                _time(timestamp),
            ),
        )
        if updated.rowcount != 1:
            raise ActivityLifecycleError("activity claim is stale or expired")
        _append_activity_input(
            conn,
            activity=_require(conn, claim.activity_id),
            operation="claim_renewed",
            timestamp=timestamp,
        )
    return claim.model_copy(update={"expires_at": expires})


def start_activity(
    conn: sqlite3.Connection,
    claim: ActivityClaim,
    *,
    now: datetime | None = None,
) -> ActivityRecord:
    timestamp = _aware(now)
    with _transaction(conn, immediate=True):
        updated = conn.execute(
            """
            UPDATE activities SET status = 'running', revision = revision + 1, updated_at = ?
            WHERE activity_id = ? AND status = 'claimed' AND attempts = ?
              AND claim_owner = ? AND claim_fence = ? AND claim_expires_at > ?
            """,
            (
                _time(timestamp),
                str(claim.activity_id),
                claim.attempt,
                claim.owner,
                claim.fence,
                _time(timestamp),
            ),
        )
        if updated.rowcount != 1:
            raise ActivityLifecycleError("activity claim is stale or expired")
        activity = _require(conn, claim.activity_id)
        _append_activity_input(
            conn,
            activity=activity,
            operation="started",
            timestamp=timestamp,
        )
    return activity


def reap_expired_claims(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    timestamp = _aware(now)
    with _transaction(conn, immediate=True):
        rows = conn.execute(
            """
            SELECT activity_id FROM activities
             WHERE status IN ('claimed','running') AND claim_expires_at <= ?
            """,
            (_time(timestamp),),
        ).fetchall()
        updated = conn.execute(
            """
            UPDATE activities
            SET status = 'waiting_admission', revision = revision + 1, claim_owner = NULL,
                claim_expires_at = NULL, updated_at = ?
            WHERE status IN ('claimed','running') AND claim_expires_at <= ?
            """,
            (_time(timestamp), _time(timestamp)),
        )
        for row in rows:
            activity_id = UUID(str(row["activity_id"]))
            _append_activity_input(
                conn,
                activity=_require(conn, activity_id),
                operation="claim_reaped",
                timestamp=timestamp,
            )
        return updated.rowcount


def reap_expired_reservations(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> int:
    timestamp = _aware(now)
    with _transaction(conn, immediate=True):
        return _release_expired_reservations(conn, timestamp)


def complete_activity(
    conn: sqlite3.Connection,
    claim: ActivityClaim,
    outcome: ActivityOutcome,
    *,
    now: datetime | None = None,
) -> ActivityResultRecord:
    """Terminalize once and wake the owning workflow in the same transaction."""

    timestamp = _aware(now)
    result = ActivityResultRecord(
        result_id=uuid4(),
        activity_id=claim.activity_id,
        attempt=claim.attempt,
        outcome=outcome,
        completed_at=timestamp,
    )
    terminal_status = _outcome_status(outcome)
    with _transaction(conn, immediate=True):
        activity = _require(conn, claim.activity_id)
        retrying = (
            isinstance(outcome, ActivityFailure)
            and outcome.retryable
            and claim.attempt < activity.max_attempts
        )
        persisted_status = ActivityStatus.WAITING_ADMISSION if retrying else terminal_status
        existing_result_row = conn.execute(
            """
            SELECT * FROM activity_results
            WHERE activity_id = ? AND attempt = ?
            """,
            (str(claim.activity_id), claim.attempt),
        ).fetchone()
        if existing_result_row is not None:
            existing = _result(existing_result_row)
            if existing.outcome != outcome:
                raise ActivityLifecycleError(
                    "activity attempt already completed with a different outcome"
                )
            return existing
        if (
            activity.status not in {ActivityStatus.CLAIMED, ActivityStatus.RUNNING}
            or activity.attempts != claim.attempt
            or activity.claimed_by != claim.owner
            or activity.claim_fence != claim.fence
            or activity.lease_expires_at is None
            or activity.lease_expires_at <= timestamp
        ):
            raise ActivityLifecycleError("completion claim/attempt is stale or expired")
        conn.execute(
            """
            INSERT INTO activity_results(
                result_id, activity_id, attempt, outcome_json, completed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(result.result_id),
                str(result.activity_id),
                result.attempt,
                _json(_OUTCOME.dump_python(outcome, mode="json")),
                _time(timestamp),
            ),
        )
        conn.execute(
            """
            UPDATE activities SET status = ?, revision = revision + 1, claim_owner = NULL,
                claim_expires_at = NULL, updated_at = ? WHERE activity_id = ?
            """,
            (persisted_status.value, _time(timestamp), str(claim.activity_id)),
        )
        conn.execute(
            "UPDATE activity_reservations SET released_at = ? WHERE activity_id = ?",
            (_time(timestamp), str(claim.activity_id)),
        )
        conn.execute(
            "DELETE FROM activity_reservation_locks WHERE activity_id = ?",
            (str(claim.activity_id),),
        )
        _append_completion_fact(
            conn,
            activity=activity,
            result_id=result.result_id,
            attempt=result.attempt,
            outcome=outcome,
            actor_kind="activity_worker",
            actor_id=claim.owner,
            timestamp=timestamp,
            kind=(
                "activity.attempt_failed"
                if retrying
                else f"activity.{terminal_status.value}"
            ),
        )
        if not retrying:
            enqueue_workflow_signal(
                conn,
                workflow_id=activity.workflow_id,
                deduplication_key=(
                    f"activity:{activity.activity_id}:attempt:{claim.attempt}:finished"
                ),
                payload=ActivityFinishedSignal(
                    activity_id=activity.activity_id,
                    result_id=result.result_id,
                ),
                created_at=timestamp,
            )
    # Wake after the durable completion transaction. Recovery owns any crash gap.
    if not retrying:
        _best_effort_wake(conn, activity.workflow_id, timestamp)
    return result


def _append_completion_fact(
    conn: sqlite3.Connection,
    *,
    activity: ActivityRecord,
    result_id: UUID,
    attempt: int,
    outcome: ActivityOutcome,
    actor_kind: str,
    actor_id: str,
    timestamp: datetime,
    kind: str | None = None,
) -> None:
    run = require_workflow_run(conn, activity.workflow_id)
    append_fact(
        conn,
        RetainedFactDraft(
            fact_id=result_id,
            kind=kind or f"activity.{_outcome_status(outcome).value}",
            occurred_at=timestamp,
            aggregate=AggregateRef(kind="activity", id=activity.activity_id, revision=attempt),
            actor=FactActor(kind=actor_kind, id=actor_id),
            correlation=FactCorrelation(
                correlation_id=run.correlation.correlation_id,
                causation_id=run.correlation.causation_id,
                trace_id=run.correlation.trace_id,
            ),
            payload={
                "workflow_id": str(activity.workflow_id),
                "result_id": str(result_id),
                "attempt": attempt,
                "outcome": _OUTCOME.dump_python(outcome, mode="json"),
            },
        ),
        projection_inputs=(
            ProjectionInputDraft(
                projection="activities",
                subject_key=str(activity.activity_id),
                generation=_require(conn, activity.activity_id).revision,
            ),
        ),
        recorded_at=timestamp,
    )


def _append_activity_input(
    conn: sqlite3.Connection,
    *,
    activity: ActivityRecord,
    operation: Literal[
        "created",
        "routed",
        "admitted",
        "claimed",
        "claim_renewed",
        "started",
        "claim_reaped",
    ],
    timestamp: datetime,
) -> None:
    run = require_workflow_run(conn, activity.workflow_id)
    payload = ActivityStateChangedPayload(
        activity_id=activity.activity_id,
        workflow_id=activity.workflow_id,
        operation=operation,
        status=activity.status.value,
        revision=activity.revision,
        attempt=activity.attempts,
        claim_fence=activity.claim_fence,
    )
    append_fact(
        conn,
        RetainedFactDraft(
            fact_id=uuid5(
                _ACTIVITY_FACT_NAMESPACE,
                f"state:{activity.activity_id}:{activity.revision}:{operation}",
            ),
            kind=f"activity.{operation}",
            occurred_at=timestamp,
            aggregate=AggregateRef(
                kind="activity",
                id=activity.activity_id,
                revision=activity.revision,
            ),
            actor=FactActor(kind="activity", id=str(activity.activity_id)),
            correlation=FactCorrelation(
                correlation_id=run.correlation.correlation_id,
                causation_id=run.correlation.causation_id,
                trace_id=run.correlation.trace_id,
            ),
            payload=payload.model_dump(mode="json"),
        ),
        projection_inputs=(
            ProjectionInputDraft(
                input_id=uuid5(
                    _ACTIVITY_FACT_NAMESPACE,
                    f"{activity.activity_id}:{activity.revision}:{operation}",
                ),
                projection="activities",
                subject_key=str(activity.activity_id),
                generation=activity.revision,
            ),
        ),
        recorded_at=timestamp,
    )


def _outcome_status(outcome: ActivityOutcome) -> ActivityStatus:
    return {
        ActivitySuccess: ActivityStatus.SUCCEEDED,
        ActivityFailure: ActivityStatus.FAILED,
        ActivityCancelled: ActivityStatus.CANCELLED,
    }[type(outcome)]


def _best_effort_wake(
    conn: sqlite3.Connection,
    workflow_id: UUID,
    timestamp: datetime,
) -> None:
    try:
        from murder.work.workflows.service import WorkflowRuntime  # noqa: PLC0415

        WorkflowRuntime(conn).decide_once(workflow_id, now=timestamp)
    except Exception:
        LOGGER.warning(
            "best-effort wake failed for workflow %s",
            workflow_id,
            exc_info=True,
        )


def _require(conn: sqlite3.Connection, activity_id: UUID) -> ActivityRecord:
    result = get_activity(conn, activity_id)
    if result is None:
        raise ActivityLifecycleError(f"activity {activity_id} does not exist")
    return result


def _release_expired_reservations(
    conn: sqlite3.Connection,
    timestamp: datetime,
) -> int:
    rows = conn.execute(
        """
        SELECT activity_id FROM activity_reservations
        WHERE released_at IS NULL AND expires_at <= ?
        """,
        (_time(timestamp),),
    ).fetchall()
    ids = [str(row["activity_id"]) for row in rows]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE activity_reservations SET released_at = ? "
        f"WHERE activity_id IN ({placeholders})",
        (_time(timestamp), *ids),
    )
    conn.execute(
        f"DELETE FROM activity_reservation_locks "
        f"WHERE activity_id IN ({placeholders})",
        tuple(ids),
    )
    return len(ids)


def _activity(row: sqlite3.Row) -> ActivityRecord:
    return ActivityRecord.model_validate(
        {
            "activity_id": row["activity_id"],
            "workflow_id": row["workflow_id"],
            "workflow_revision": row["workflow_revision"],
            "ordinal": row["ordinal"],
            "revision": row["revision"],
            "status": row["status"],
            "payload": json.loads(str(row["payload_json"])),
            "requirements": json.loads(str(row["requirements_json"])),
            "idempotency_key": row["idempotency_key"],
            "priority": row["priority"],
            "retry_policy": row["retry_policy"],
            "max_attempts": row["max_attempts"],
            "route": json.loads(str(row["route_json"])) if row["route_json"] else None,
            "route_id": row["route_id"],
            "session_id": row["session_id"],
            "attempts": row["attempts"],
            "claimed_by": row["claim_owner"],
            "claim_fence": row["claim_fence"],
            "lease_expires_at": row["claim_expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _result(row: sqlite3.Row) -> ActivityResultRecord:
    return ActivityResultRecord.model_validate(
        {
            "result_id": row["result_id"],
            "activity_id": row["activity_id"],
            "attempt": row["attempt"],
            "outcome": json.loads(str(row["outcome_json"])),
            "completed_at": row["completed_at"],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("activity timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _time(value: datetime) -> str:
    return _aware(value).isoformat(timespec="microseconds")


def _parse_time(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))


@contextmanager
def _transaction(conn: sqlite3.Connection, *, immediate: bool = False) -> Iterator[None]:
    if conn.in_transaction:
        name = f"activity_{uuid4().hex}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        else:
            conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
