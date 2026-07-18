"""Atomic deduplicated trigger firing."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from murder.state.persistence.workflow_runs import enqueue_workflow_signal
from murder.work.triggers.runtime import (
    ManualTrigger,
    SignalWorkflowTarget,
    StartWorkflowTarget,
    TriggerFiringRecord,
    TriggerRecord,
    TriggerSpec,
    TriggerTarget,
)
from murder.work.workflows.runtime import ExternalWorkflowSignal

LOGGER = logging.getLogger(__name__)
_SPEC: TypeAdapter[TriggerSpec] = TypeAdapter(TriggerSpec)
_TARGET: TypeAdapter[TriggerTarget] = TypeAdapter(TriggerTarget)
StartWorkflow = Callable[[sqlite3.Connection, StartWorkflowTarget, datetime], UUID]


def create_trigger(conn: sqlite3.Connection, trigger: TriggerRecord) -> None:
    conn.execute(
        """
        INSERT INTO workflow_triggers(
            trigger_id, name, version, dedup_window_seconds,
            spec_json, target_json, enabled, created_at, last_fired_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(trigger.trigger_id),
            trigger.name,
            trigger.version,
            trigger.dedup_window_seconds,
            _json(_SPEC.dump_python(trigger.spec, mode="json")),
            _json(_TARGET.dump_python(trigger.target, mode="json")),
            int(trigger.enabled),
            _time(trigger.created_at),
            _time(trigger.last_fired_at) if trigger.last_fired_at else None,
        ),
    )


def list_triggers(conn: sqlite3.Connection) -> tuple[TriggerRecord, ...]:
    rows = conn.execute(
        "SELECT * FROM workflow_triggers WHERE enabled = 1 ORDER BY created_at, trigger_id"
    ).fetchall()
    return tuple(_trigger(row) for row in rows)


def get_trigger(conn: sqlite3.Connection, trigger_id: UUID) -> TriggerRecord | None:
    row = conn.execute(
        "SELECT * FROM workflow_triggers WHERE trigger_id = ?",
        (str(trigger_id),),
    ).fetchone()
    return None if row is None else _trigger(row)


def enqueue_manual_trigger_fire(
    conn: sqlite3.Connection,
    trigger_id: UUID,
    *,
    occurrence_key: str | None = None,
    now: datetime | None = None,
) -> str:
    """Persist a pending manual fire; returns the occurrence key."""

    trigger = get_trigger(conn, trigger_id)
    if trigger is None:
        raise ValueError(f"trigger {trigger_id} does not exist")
    if not trigger.enabled:
        raise ValueError(f"trigger {trigger_id} is disabled")
    if not isinstance(trigger.spec, ManualTrigger):
        raise ValueError(f"trigger {trigger_id} is not a manual trigger")
    key = occurrence_key or f"manual:{uuid4()}"
    if not key:
        raise ValueError("occurrence_key must not be empty")
    timestamp = _aware(now)
    with _transaction(conn):
        conn.execute(
            """
            INSERT INTO trigger_manual_pending(trigger_id, occurrence_key, enqueued_at)
            VALUES (?, ?, ?)
            ON CONFLICT(trigger_id, occurrence_key) DO NOTHING
            """,
            (str(trigger_id), key, _time(timestamp)),
        )
    return key


def list_pending_manual_fires(conn: sqlite3.Connection, trigger_id: UUID) -> tuple[str, ...]:
    """Pending manual occurrence keys not yet recorded in ``trigger_firings``."""

    rows = conn.execute(
        """
        SELECT p.occurrence_key AS occurrence_key
          FROM trigger_manual_pending AS p
         WHERE p.trigger_id = ?
           AND NOT EXISTS (
                SELECT 1 FROM trigger_firings AS f
                 WHERE f.trigger_id = p.trigger_id
                   AND f.occurrence_key = p.occurrence_key
           )
         ORDER BY p.enqueued_at, p.occurrence_key
        """,
        (str(trigger_id),),
    ).fetchall()
    return tuple(str(row["occurrence_key"]) for row in rows)


def fire_trigger(
    conn: sqlite3.Connection,
    trigger_id: UUID,
    *,
    occurrence_key: str,
    start_workflow: StartWorkflow,
    now: datetime | None = None,
) -> TriggerFiringRecord:
    """Deduplicate occurrence and perform its workflow action atomically."""

    if not occurrence_key:
        raise ValueError("occurrence_key must not be empty")
    timestamp = _aware(now)
    wake_workflow_id: UUID | None = None
    with _transaction(conn):
        existing = conn.execute(
            """
            SELECT * FROM trigger_firings
            WHERE trigger_id = ? AND occurrence_key = ?
            """,
            (str(trigger_id), occurrence_key),
        ).fetchone()
        if existing is not None:
            return _firing(existing)
        row = conn.execute(
            "SELECT * FROM workflow_triggers WHERE trigger_id = ? AND enabled = 1",
            (str(trigger_id),),
        ).fetchone()
        if row is None:
            raise ValueError("trigger does not exist or is disabled")
        dedup_window = int(row["dedup_window_seconds"])
        if dedup_window:
            recent = conn.execute(
                """
                SELECT * FROM trigger_firings
                WHERE trigger_id = ? AND fired_at > ?
                ORDER BY fired_at DESC LIMIT 1
                """,
                (
                    str(trigger_id),
                    _time(timestamp - timedelta(seconds=dedup_window)),
                ),
            ).fetchone()
            if recent is not None:
                return _firing(recent)
        target = _TARGET.validate_python(json.loads(str(row["target_json"])))
        if isinstance(target, StartWorkflowTarget):
            workflow_id = start_workflow(conn, target, timestamp)
            wake_workflow_id = workflow_id
        elif isinstance(target, SignalWorkflowTarget):
            enqueue_workflow_signal(
                conn,
                workflow_id=target.workflow_id,
                deduplication_key=f"trigger:{trigger_id}:{occurrence_key}",
                payload=ExternalWorkflowSignal(
                    name=target.signal_name,
                    correlation_key=target.correlation_key,
                    payload=target.payload,
                ),
                created_at=timestamp,
            )
            workflow_id = target.workflow_id
            wake_workflow_id = workflow_id
        else:
            raise AssertionError("closed trigger target")
        firing = TriggerFiringRecord(
            firing_id=uuid4(),
            trigger_id=trigger_id,
            occurrence_key=occurrence_key,
            fired_at=timestamp,
            workflow_id=workflow_id,
        )
        conn.execute(
            """
            INSERT INTO trigger_firings(
                firing_id, trigger_id, occurrence_key, fired_at, workflow_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(firing.firing_id),
                str(trigger_id),
                occurrence_key,
                _time(timestamp),
                str(workflow_id),
            ),
        )
        conn.execute(
            "UPDATE workflow_triggers SET last_fired_at = ? WHERE trigger_id = ?",
            (_time(timestamp), str(trigger_id)),
        )
    if wake_workflow_id is not None:
        try:
            from murder.work.workflows.service import WorkflowRuntime  # noqa: PLC0415

            WorkflowRuntime(conn).decide_once(wake_workflow_id, now=timestamp)
        except Exception:
            LOGGER.warning(
                "best-effort wake failed for workflow %s after trigger fire",
                wake_workflow_id,
                exc_info=True,
            )
    return firing


def _firing(row: sqlite3.Row) -> TriggerFiringRecord:
    return TriggerFiringRecord.model_validate(dict(row))


def _trigger(row: sqlite3.Row) -> TriggerRecord:
    return TriggerRecord.model_validate(
        {
            "trigger_id": row["trigger_id"],
            "name": row["name"],
            "version": row["version"],
            "dedup_window_seconds": row["dedup_window_seconds"],
            "spec": json.loads(str(row["spec_json"])),
            "target": json.loads(str(row["target_json"])),
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
            "last_fired_at": row["last_fired_at"],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("trigger timestamps must be timezone-aware")
    return result.astimezone(timezone.utc)


def _time(value: datetime) -> str:
    return _aware(value).isoformat(timespec="microseconds")


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        name = f"trigger_{uuid4().hex}"
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
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
