"""Atomic SQLite persistence for sessions and fenced writer leases.

`SESSION_SCHEMA_SQL` is deliberately feature-owned.  The central schema and
migration files can include/call it without making this module depend on their
release cadence.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    HarnessSessionRecord,
    LeaseResource,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RenewWriterLease,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLease,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterLeaseReply,
    WriterMode,
)

SESSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harness_sessions (
    session_id             TEXT PRIMARY KEY,
    agent_id               TEXT,
    repository_id          TEXT NOT NULL,
    harness                TEXT NOT NULL,
    model                  TEXT,
    effort                 TEXT,
    transport              TEXT NOT NULL CHECK (transport IN ('tmux','app_server','subprocess')),
    transport_ref          TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK (status IN (
                               'starting','ready','working','awaiting_input',
                               'awaiting_approval','stopping','stopped','failed','lost')),
    revision               INTEGER NOT NULL CHECK (revision >= 0),
    capabilities_json      TEXT NOT NULL,
    owning_workflow_id     TEXT,
    owning_activity_id     TEXT,
    started_at             TEXT NOT NULL,
    last_observed_at       TEXT,
    stopped_at             TEXT
);

CREATE INDEX IF NOT EXISTS idx_harness_sessions_status
    ON harness_sessions(status);
CREATE INDEX IF NOT EXISTS idx_harness_sessions_agent
    ON harness_sessions(agent_id);

CREATE TABLE IF NOT EXISTS session_writer_fences (
    session_id             TEXT PRIMARY KEY
                           REFERENCES harness_sessions(session_id) ON DELETE CASCADE,
    last_fence             INTEGER NOT NULL CHECK (last_fence >= 0)
);

CREATE TABLE IF NOT EXISTS writer_leases (
    lease_id               TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL
                           REFERENCES harness_sessions(session_id) ON DELETE CASCADE,
    holder_kind            TEXT NOT NULL CHECK (holder_kind IN (
                               'user','client','workflow','service','reviewer')),
    holder_id              TEXT NOT NULL,
    mode                   TEXT NOT NULL CHECK (mode IN ('structured','raw_terminal')),
    fence                  INTEGER NOT NULL CHECK (fence >= 1),
    issued_at              TEXT NOT NULL,
    renewed_at             TEXT NOT NULL,
    expires_at             TEXT NOT NULL,
    revoked_at             TEXT,
    revocation_reason      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_writer_leases_session_fence
    ON writer_leases(session_id, fence);
CREATE INDEX IF NOT EXISTS idx_writer_leases_active
    ON writer_leases(session_id, revoked_at, expires_at);

CREATE TABLE IF NOT EXISTS writer_lease_audit_facts (
    fact_id                TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL
                           REFERENCES harness_sessions(session_id) ON DELETE CASCADE,
    fact_kind              TEXT NOT NULL CHECK (fact_kind IN ('writer_force_takeover')),
    occurred_at            TEXT NOT NULL,
    payload_json           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_writer_lease_audit_session
    ON writer_lease_audit_facts(session_id, occurred_at);
"""


def ensure_session_schema(connection: sqlite3.Connection) -> None:
    """Create the feature-owned tables idempotently."""

    connection.executescript(SESSION_SCHEMA_SQL)


class SessionPersistenceError(RuntimeError):
    """Base class for persistent session invariant failures."""


class SessionNotFoundError(SessionPersistenceError):
    pass


class SessionRevisionConflictError(SessionPersistenceError):
    pass


class StaleWriterLeaseError(SessionPersistenceError):
    pass


class WriterLeaseRequiredError(SessionPersistenceError):
    pass


class SessionStore:
    """DAO whose lease decisions are single SQLite transactions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def save_session(
        self,
        record: HarnessSessionRecord,
        *,
        expected_revision: int | None = None,
    ) -> None:
        with self._atomic():
            existing = self._connection.execute(
                "SELECT revision FROM harness_sessions WHERE session_id = ?",
                (str(record.session_id),),
            ).fetchone()
            if expected_revision is not None:
                actual = None if existing is None else int(existing[0])
                if actual != expected_revision:
                    raise SessionRevisionConflictError(
                        f"session revision is {actual}, expected {expected_revision}"
                    )
            self._connection.execute(
                """
                INSERT INTO harness_sessions (
                    session_id, agent_id, repository_id, harness, model, effort,
                    transport, transport_ref, status, revision, capabilities_json,
                    owning_workflow_id, owning_activity_id, started_at,
                    last_observed_at, stopped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent_id = excluded.agent_id,
                    repository_id = excluded.repository_id,
                    harness = excluded.harness,
                    model = excluded.model,
                    effort = excluded.effort,
                    transport = excluded.transport,
                    transport_ref = excluded.transport_ref,
                    status = excluded.status,
                    revision = excluded.revision,
                    capabilities_json = excluded.capabilities_json,
                    owning_workflow_id = excluded.owning_workflow_id,
                    owning_activity_id = excluded.owning_activity_id,
                    started_at = excluded.started_at,
                    last_observed_at = excluded.last_observed_at,
                    stopped_at = excluded.stopped_at
                """,
                _session_values(record),
            )
            self._connection.execute(
                """
                INSERT INTO session_writer_fences(session_id, last_fence)
                VALUES (?, 0)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (str(record.session_id),),
            )

    def get_session(self, session_id: UUID) -> HarnessSessionRecord | None:
        row = self._connection.execute(
            """
            SELECT session_id, agent_id, repository_id, harness, model, effort,
                   transport, transport_ref, status, revision, capabilities_json,
                   owning_workflow_id, owning_activity_id, started_at,
                   last_observed_at, stopped_at
            FROM harness_sessions
            WHERE session_id = ?
            """,
            (str(session_id),),
        ).fetchone()
        return None if row is None else _session_from_row(row)

    def list_recoverable_sessions(self) -> tuple[HarnessSessionRecord, ...]:
        rows = self._connection.execute(
            """
            SELECT session_id, agent_id, repository_id, harness, model, effort,
                   transport, transport_ref, status, revision, capabilities_json,
                   owning_workflow_id, owning_activity_id, started_at,
                   last_observed_at, stopped_at
            FROM harness_sessions
            WHERE status NOT IN ('stopped', 'failed', 'lost')
            ORDER BY started_at, session_id
            """
        ).fetchall()
        return tuple(_session_from_row(row) for row in rows)

    def acquire_writer_lease(
        self,
        request: AcquireWriterLease,
        *,
        holder: PrincipalRef,
        force_authorized: bool = False,
        revocation_reason: str | None = None,
    ) -> WriterLeaseReply:
        now = _aware(self._clock())
        with self._atomic():
            self._require_session(request.session_id)
            if request.meta.expected_revision is not None:
                revision_row = self._connection.execute(
                    "SELECT revision FROM harness_sessions WHERE session_id = ?",
                    (str(request.session_id),),
                ).fetchone()
                assert revision_row is not None
                if int(revision_row[0]) != request.meta.expected_revision:
                    return WriterLeaseDenied(
                        request_id=request.meta.request_id,
                        reason=(
                            f"session revision is {int(revision_row[0])}, "
                            f"expected {request.meta.expected_revision}"
                        ),
                    )
            current = self._active_lease_row(request.session_id, now)
            if current is not None and not request.force:
                lease = _lease_from_row(current)
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    current_holder=lease.holder,
                    current_mode=lease.mode,
                    retry_after=lease.expires_at,
                    reason="session already has an active writer",
                )
            if current is not None and request.force and not force_authorized:
                lease = _lease_from_row(current)
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    current_holder=lease.holder,
                    current_mode=lease.mode,
                    retry_after=lease.expires_at,
                    reason="force takeover requires an explicit permission decision",
                )
            if request.force and not force_authorized:
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    reason="force acquisition requires an explicit permission decision",
                )
            if current is not None:
                self._connection.execute(
                    """
                    UPDATE writer_leases
                    SET revoked_at = ?, revocation_reason = ?
                    WHERE lease_id = ? AND revoked_at IS NULL
                    """,
                    (
                        _dump_time(now),
                        revocation_reason or f"force takeover by {holder.kind.value}:{holder.id}",
                        str(current[0]),
                    ),
                )
            fence = self._next_fence(request.session_id)
            lease = WriterLease(
                lease_id=uuid4(),
                resource=LeaseResource(session_id=request.session_id),
                holder=holder,
                mode=request.mode,
                fence=fence,
                issued_at=now,
                renewed_at=now,
                expires_at=now + timedelta(seconds=request.ttl_seconds),
            )
            self._insert_lease(lease)
            if current is not None:
                previous = _lease_from_row(current)
                self._connection.execute(
                    """
                    INSERT INTO writer_lease_audit_facts (
                        fact_id, session_id, fact_kind, occurred_at, payload_json
                    ) VALUES (?, ?, 'writer_force_takeover', ?, ?)
                    """,
                    (
                        str(uuid4()),
                        str(request.session_id),
                        _dump_time(now),
                        json.dumps(
                            {
                                "previous_lease_id": str(previous.lease_id),
                                "previous_fence": previous.fence,
                                "new_lease_id": str(lease.lease_id),
                                "new_fence": lease.fence,
                                "holder_kind": holder.kind.value,
                                "holder_id": holder.id,
                                "revocation_reason": (
                                    revocation_reason
                                    or f"force takeover by {holder.kind.value}:{holder.id}"
                                ),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            return WriterLeaseGranted(request_id=request.meta.request_id, lease=lease)

    def renew_writer_lease(
        self,
        request: RenewWriterLease,
        *,
        holder: PrincipalRef,
    ) -> WriterLeaseReply:
        now = _aware(self._clock())
        with self._atomic():
            row = self._connection.execute(
                _LEASE_SELECT + " WHERE lease_id = ?",
                (str(request.lease_id),),
            ).fetchone()
            if row is None:
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    reason="writer lease does not exist",
                )
            lease = _lease_from_row(row)
            if (
                lease.fence != request.fence
                or lease.holder != holder
                or lease.revoked_at is not None
                or lease.expires_at <= now
            ):
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    current_holder=lease.holder,
                    current_mode=lease.mode,
                    retry_after=lease.expires_at if lease.expires_at > now else None,
                    reason="writer lease is stale, expired, revoked, or held by another principal",
                )
            renewed = lease.model_copy(
                update={
                    "renewed_at": now,
                    "expires_at": now + timedelta(seconds=request.ttl_seconds),
                }
            )
            self._connection.execute(
                "UPDATE writer_leases SET renewed_at = ?, expires_at = ? WHERE lease_id = ?",
                (
                    _dump_time(renewed.renewed_at),
                    _dump_time(renewed.expires_at),
                    str(renewed.lease_id),
                ),
            )
            return WriterLeaseGranted(request_id=request.meta.request_id, lease=renewed)

    def release_writer_lease(
        self,
        request: ReleaseWriterLease,
        *,
        holder: PrincipalRef,
        reason: str = "released by holder",
    ) -> WriterLeaseReply:
        now = _aware(self._clock())
        with self._atomic():
            row = self._connection.execute(
                _LEASE_SELECT + " WHERE lease_id = ?",
                (str(request.lease_id),),
            ).fetchone()
            if row is None:
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    reason="writer lease does not exist",
                )
            lease = _lease_from_row(row)
            if (
                lease.fence != request.fence
                or lease.holder != holder
                or lease.revoked_at is not None
            ):
                return WriterLeaseDenied(
                    request_id=request.meta.request_id,
                    current_holder=lease.holder,
                    current_mode=lease.mode,
                    reason="writer lease is stale, revoked, or held by another principal",
                )
            self._connection.execute(
                """
                UPDATE writer_leases
                SET revoked_at = ?, revocation_reason = ?
                WHERE lease_id = ? AND fence = ? AND revoked_at IS NULL
                """,
                (_dump_time(now), reason, str(request.lease_id), request.fence),
            )
            released = lease.model_copy(update={"revoked_at": now, "revocation_reason": reason})
            return WriterLeaseGranted(request_id=request.meta.request_id, lease=released)

    def active_writer_lease(
        self,
        session_id: UUID,
        *,
        at: datetime | None = None,
    ) -> WriterLease | None:
        row = self._active_lease_row(session_id, _aware(at or self._clock()))
        return None if row is None else _lease_from_row(row)

    def revoke_session_writer_leases(
        self,
        session_id: UUID,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> int:
        """Fence off every outstanding writer when a session stops or is lost."""

        now = _aware(at or self._clock())
        with self._atomic():
            self._require_session(session_id)
            cursor = self._connection.execute(
                """
                UPDATE writer_leases
                SET revoked_at = ?, revocation_reason = ?
                WHERE session_id = ? AND revoked_at IS NULL
                """,
                (_dump_time(now), reason, str(session_id)),
            )
            return cursor.rowcount

    def validate_writer_lease(
        self,
        *,
        session_id: UUID,
        lease_id: UUID,
        fence: int,
        holder: PrincipalRef,
        required_mode: WriterMode | None = None,
        at: datetime | None = None,
    ) -> WriterLease:
        """Reject any former holder by checking the current session fence too."""

        now = _aware(at or self._clock())
        row = self._connection.execute(
            _LEASE_SELECT
            + """
              WHERE lease_id = ? AND session_id = ?
                AND fence = ? AND revoked_at IS NULL AND expires_at > ?
            """,
            (str(lease_id), str(session_id), fence, _dump_time(now)),
        ).fetchone()
        if row is None:
            raise StaleWriterLeaseError("writer lease is stale, expired, revoked, or unknown")
        lease = _lease_from_row(row)
        if lease.holder != holder:
            raise StaleWriterLeaseError("writer lease belongs to another principal")
        if required_mode is not None and lease.mode is not required_mode:
            raise StaleWriterLeaseError(
                f"writer lease mode is {lease.mode.value}, expected {required_mode.value}"
            )
        fence_row = self._connection.execute(
            "SELECT last_fence FROM session_writer_fences WHERE session_id = ?",
            (str(session_id),),
        ).fetchone()
        if fence_row is None or int(fence_row[0]) != fence:
            raise StaleWriterLeaseError("writer lease fence has been superseded")
        return lease

    def _require_session(self, session_id: UUID) -> None:
        if (
            self._connection.execute(
                "SELECT 1 FROM harness_sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
            is None
        ):
            raise SessionNotFoundError(f"session {session_id} does not exist")

    def _active_lease_row(
        self, session_id: UUID, at: datetime
    ) -> sqlite3.Row | tuple[Any, ...] | None:
        row = self._connection.execute(
            _LEASE_SELECT
            + """
              WHERE session_id = ? AND revoked_at IS NULL AND expires_at > ?
              ORDER BY fence DESC LIMIT 1
            """,
            (str(session_id), _dump_time(at)),
        ).fetchone()
        return cast("sqlite3.Row | tuple[Any, ...] | None", row)

    def _next_fence(self, session_id: UUID) -> int:
        row = self._connection.execute(
            "SELECT last_fence FROM session_writer_fences WHERE session_id = ?",
            (str(session_id),),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(f"session {session_id} has no fence counter")
        fence = int(row[0]) + 1
        self._connection.execute(
            "UPDATE session_writer_fences SET last_fence = ? WHERE session_id = ?",
            (fence, str(session_id)),
        )
        return fence

    def _insert_lease(self, lease: WriterLease) -> None:
        self._connection.execute(
            """
            INSERT INTO writer_leases (
                lease_id, session_id, holder_kind, holder_id, mode, fence,
                issued_at, renewed_at, expires_at, revoked_at, revocation_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(lease.lease_id),
                str(lease.resource.session_id),
                lease.holder.kind.value,
                lease.holder.id,
                lease.mode.value,
                lease.fence,
                _dump_time(lease.issued_at),
                _dump_time(lease.renewed_at),
                _dump_time(lease.expires_at),
                None,
                None,
            ),
        )

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """Use a savepoint so callers may already own a broader transaction."""

        name = f"session_store_{uuid4().hex}"
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            self._connection.execute(f"RELEASE SAVEPOINT {name}")


_LEASE_SELECT = """
SELECT lease_id, session_id, holder_kind, holder_id, mode, fence,
       issued_at, renewed_at, expires_at, revoked_at, revocation_reason
FROM writer_leases
"""


def _session_values(record: HarnessSessionRecord) -> tuple[object, ...]:
    return (
        str(record.session_id),
        str(record.agent_id) if record.agent_id else None,
        str(record.repository_id),
        record.harness,
        record.model,
        record.effort,
        record.transport.value,
        record.transport_ref,
        record.status.value,
        record.revision,
        record.capabilities.model_dump_json(),
        str(record.owning_workflow_id) if record.owning_workflow_id else None,
        str(record.owning_activity_id) if record.owning_activity_id else None,
        _dump_time(record.started_at),
        _dump_time(record.last_observed_at) if record.last_observed_at else None,
        _dump_time(record.stopped_at) if record.stopped_at else None,
    )


def _session_from_row(row: sqlite3.Row | tuple[Any, ...]) -> HarnessSessionRecord:
    return HarnessSessionRecord(
        session_id=UUID(row[0]),
        agent_id=UUID(row[1]) if row[1] else None,
        repository_id=UUID(row[2]),
        harness=row[3],
        model=row[4],
        effort=row[5],
        transport=SessionTransport(row[6]),
        transport_ref=row[7],
        status=SessionStatus(row[8]),
        revision=int(row[9]),
        capabilities=SessionCapabilities.model_validate(json.loads(row[10])),
        owning_workflow_id=UUID(row[11]) if row[11] else None,
        owning_activity_id=UUID(row[12]) if row[12] else None,
        started_at=_load_time(row[13]),
        last_observed_at=_load_time(row[14]) if row[14] else None,
        stopped_at=_load_time(row[15]) if row[15] else None,
    )


def _lease_from_row(row: sqlite3.Row | tuple[Any, ...]) -> WriterLease:
    return WriterLease(
        lease_id=UUID(row[0]),
        resource=LeaseResource(session_id=UUID(row[1])),
        holder=PrincipalRef(kind=PrincipalKind(row[2]), id=row[3]),
        mode=WriterMode(row[4]),
        fence=int(row[5]),
        issued_at=_load_time(row[6]),
        renewed_at=_load_time(row[7]),
        expires_at=_load_time(row[8]),
        revoked_at=_load_time(row[9]) if row[9] else None,
        revocation_reason=row[10],
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("session persistence clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _dump_time(value: datetime) -> str:
    return _aware(value).isoformat()


def _load_time(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


__all__ = [
    "SESSION_SCHEMA_SQL",
    "SessionNotFoundError",
    "SessionPersistenceError",
    "SessionRevisionConflictError",
    "SessionStore",
    "StaleWriterLeaseError",
    "WriterLeaseRequiredError",
    "ensure_session_schema",
]
