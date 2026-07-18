"""Synchronous owner for loading, deciding, and applying workflow transitions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, cast
from uuid import UUID

from murder.state.persistence.workflow_runs import (
    StaleWorkflowRevisionError,
    apply_transition_plan,
    apply_workflow_state_migration,
    enqueue_workflow_signal,
    list_workflow_runs,
    load_workflow_decision_input,
)
from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.runtime import (
    ExternalWorkflowSignal,
    VersionedState,
    WorkflowContract,
    WorkflowMachine,
    WorkflowRunRecord,
    WorkflowSignalPayload,
    WorkflowSignalRecord,
    WorkflowStateMigrationRecord,
    WorkflowStatus,
)
from murder.work.workflows.static_dag import StaticDagWorkflowMachine


class WorkflowDefinitionUnavailableError(RuntimeError):
    """The persisted definition/version cannot be interpreted by this service."""


class WorkflowMachineResolver(Protocol):
    def __call__(self, run: WorkflowRunRecord) -> WorkflowMachine[WorkflowContract]: ...


@dataclass(frozen=True, slots=True)
class WorkflowMachineKey:
    definition_name: str
    definition_version: int
    state_schema_name: str
    state_schema_version: int

    @classmethod
    def from_run(cls, run: WorkflowRunRecord) -> WorkflowMachineKey:
        return cls(
            run.definition_name,
            run.definition_version,
            run.state.schema_name,
            run.state.schema_version,
        )


class WorkflowMachineRegistry:
    """Exact compatibility registry; unknown persisted semantics never fall through."""

    def __init__(self) -> None:
        self._machines: dict[WorkflowMachineKey, WorkflowMachine[WorkflowContract]] = {}

    def register(
        self,
        key: WorkflowMachineKey,
        machine: WorkflowMachine[WorkflowContract],
    ) -> None:
        if key in self._machines:
            raise ValueError(f"workflow machine already registered for {key}")
        self._machines[key] = machine

    def resolve(self, run: WorkflowRunRecord) -> WorkflowMachine[WorkflowContract]:
        key = WorkflowMachineKey.from_run(run)
        try:
            return self._machines[key]
        except KeyError as exc:
            raise WorkflowDefinitionUnavailableError(
                f"no workflow machine registered for {key}"
            ) from exc


def resolve_persisted_machine(
    run: WorkflowRunRecord,
) -> WorkflowMachine[WorkflowContract]:
    """Resolve only explicitly versioned, persisted definition snapshots."""

    if (
        run.state.schema_name != "static_dag"
        or run.state.schema_version != 1
        or run.definition_snapshot is None
    ):
        raise WorkflowDefinitionUnavailableError(
            f"no machine for {run.definition_name!r} v{run.definition_version} "
            f"state {run.state.schema_name!r} v{run.state.schema_version}"
        )
    definition = WorkflowDef.model_validate(run.definition_snapshot)
    if definition.definition_version != run.definition_version:
        raise WorkflowDefinitionUnavailableError(
            "persisted workflow definition version does not match its run envelope"
        )
    # StaticDagWorkflowStateV1 is a WorkflowContract subtype; the resolver's
    # erased protocol type is intentional at this runtime registry boundary.
    machine = cast(
        WorkflowMachine[WorkflowContract],
        StaticDagWorkflowMachine(definition, run.stage_map),
    )
    registry = WorkflowMachineRegistry()
    registry.register(WorkflowMachineKey.from_run(run), machine)
    return registry.resolve(run)


class WorkflowRuntime:
    """Run one finite pure decision and atomically persist its plan."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        resolver: WorkflowMachineResolver = resolve_persisted_machine,
        max_conflict_retries: int = 3,
    ) -> None:
        if max_conflict_retries < 0:
            raise ValueError("max_conflict_retries must not be negative")
        self._connection = connection
        self._resolver = resolver
        self._max_conflict_retries = max_conflict_retries

    def decide_once(
        self,
        workflow_id: UUID,
        *,
        now: datetime | None = None,
    ) -> WorkflowRunRecord:
        for attempt in range(self._max_conflict_retries + 1):
            decision = load_workflow_decision_input(
                self._connection,
                workflow_id,
                now=now,
            )
            if decision.run.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            }:
                return decision.run
            machine = self._resolver(decision.run)
            state = machine.state_model.model_validate(decision.run.state.value)
            plan = machine.decide(
                state=state,
                waits=decision.waits,
                signals=decision.signals,
                now=decision.now,
                current_revision=decision.run.revision,
            )
            try:
                return apply_transition_plan(
                    self._connection,
                    workflow_id=workflow_id,
                    plan=plan,
                    applied_at=decision.now,
                )
            except StaleWorkflowRevisionError:
                if attempt >= self._max_conflict_retries:
                    raise
        raise AssertionError("bounded workflow retry loop did not terminate")

    def recover_pending_signals(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[WorkflowRunRecord, ...]:
        """Wake persisted nonterminal workflows after restart or missed notification."""

        if limit < 1:
            raise ValueError("limit must be positive")
        recovered: dict[UUID, WorkflowRunRecord] = {}
        blocked: set[UUID] = set()
        while True:
            blocked_sql = ""
            parameters: list[object] = []
            if blocked:
                placeholders = ",".join("?" for _ in blocked)
                blocked_sql = f" AND r.workflow_id NOT IN ({placeholders})"
                parameters.extend(str(workflow_id) for workflow_id in sorted(blocked, key=str))
            parameters.append(limit)
            rows = self._connection.execute(
                f"""
                SELECT r.workflow_id, COUNT(*) AS pending_count
                FROM workflow_runs AS r
                JOIN workflow_signals AS s ON s.workflow_id = r.workflow_id
                WHERE s.consumed_at IS NULL
                  AND r.status IN ('running', 'waiting')
                  {blocked_sql}
                GROUP BY r.workflow_id
                ORDER BY r.updated_at, r.workflow_id
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                workflow_id = UUID(str(row["workflow_id"]))
                pending_before = int(row["pending_count"])
                try:
                    updated = self.decide_once(workflow_id, now=now)
                except WorkflowDefinitionUnavailableError:
                    # Unknown persisted semantics remain durable and untouched
                    # until their implementation is installed.
                    blocked.add(workflow_id)
                    continue
                recovered[workflow_id] = updated
                pending_after = _pending_signal_count(self._connection, workflow_id)
                if pending_after >= pending_before:
                    # The machine deliberately left this wakeup pending. Do not
                    # spin or starve later workflow pages during this recovery.
                    blocked.add(workflow_id)
        return tuple(recovered.values())

    def enqueue_and_wake(
        self,
        *,
        workflow_id: UUID,
        deduplication_key: str,
        payload: WorkflowSignalPayload,
        created_at: datetime | None = None,
    ) -> tuple[WorkflowSignalRecord, WorkflowRunRecord]:
        """Durably enqueue one addressed signal, then notify its local runtime."""

        signal = enqueue_workflow_signal(
            self._connection,
            workflow_id=workflow_id,
            deduplication_key=deduplication_key,
            payload=payload,
            created_at=created_at,
        )
        if signal.consumed_at is not None:
            decision = load_workflow_decision_input(
                self._connection,
                workflow_id,
                now=created_at,
            )
            return signal, decision.run
        return signal, self.decide_once(workflow_id, now=created_at)

    def migrate_state(
        self,
        workflow_id: UUID,
        *,
        expected_revision: int,
        target_state: VersionedState,
        migration_name: str,
        now: datetime | None = None,
    ) -> WorkflowStateMigrationRecord:
        """Apply an explicit state-schema migration and record its provenance."""

        run = load_workflow_decision_input(self._connection, workflow_id, now=now).run
        if run.revision != expected_revision:
            raise StaleWorkflowRevisionError(workflow_id, expected_revision, run.revision)
        candidate = run.model_copy(update={"state": target_state})
        # The target triple must be registered/resolvable before persistence.
        machine = self._resolver(candidate)
        machine.state_model.model_validate(target_state.value)
        return apply_workflow_state_migration(
            self._connection,
            workflow_id=workflow_id,
            expected_revision=expected_revision,
            target_state=target_state,
            migration_name=migration_name,
            migrated_at=now,
        )

    def signal_ticket_finished(
        self,
        *,
        ticket_id: str,
        status: str,
        occurred_at: datetime | None = None,
    ) -> tuple[WorkflowRunRecord, ...]:
        """Address a terminal ticket outcome to each owning static-DAG run."""

        timestamp = _aware(occurred_at)
        updated: list[WorkflowRunRecord] = []
        for run in list_workflow_runs(self._connection):
            if run.status in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            }:
                continue
            if ticket_id not in run.stage_map.values():
                continue
            signal = enqueue_workflow_signal(
                self._connection,
                workflow_id=run.workflow_id,
                deduplication_key=f"ticket:{ticket_id}:terminal:{status}",
                payload=ExternalWorkflowSignal(
                    name="ticket.finished",
                    correlation_key=ticket_id,
                    payload={"status": status},
                ),
                created_at=timestamp,
            )
            if signal.consumed_at is None:
                updated.append(self.decide_once(run.workflow_id, now=timestamp))
            else:
                updated.append(run)
        return tuple(updated)


def notify_ticket_status(
    connection: sqlite3.Connection,
    *,
    ticket_id: str,
    status: str,
) -> tuple[WorkflowRunRecord, ...]:
    """Compatibility-ticket choke point for the persisted workflow runtime."""

    if status not in {"done", "failed", "archived"}:
        return ()
    try:
        return WorkflowRuntime(connection).signal_ticket_finished(
            ticket_id=ticket_id,
            status=status,
        )
    except sqlite3.OperationalError as exc:
        # Tiny unit schemas and pre-migration administrative tools may not own
        # workflow tables. Do not turn an unrelated ticket write into a crash.
        if "no such table" not in str(exc):
            raise
        return ()


def _aware(value: datetime | None) -> datetime:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("workflow runtime requires a timezone-aware timestamp")
    return instant.astimezone(timezone.utc)


def _pending_signal_count(connection: sqlite3.Connection, workflow_id: UUID) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM workflow_signals
        WHERE workflow_id = ? AND consumed_at IS NULL
        """,
        (str(workflow_id),),
    ).fetchone()
    return int(row["count"])


__all__ = [
    "WorkflowDefinitionUnavailableError",
    "WorkflowMachineKey",
    "WorkflowMachineRegistry",
    "WorkflowRuntime",
    "notify_ticket_status",
    "resolve_persisted_machine",
]
