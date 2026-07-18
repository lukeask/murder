from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RenewWriterLease,
    RequestMeta,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterMode,
)
from murder.runtime.sessions.persistence import (
    SessionStore,
    StaleWriterLeaseError,
    ensure_session_schema,
)

SECOND_FENCE = 2


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def meta(*, expected_revision: int | None = None) -> RequestMeta:
    return RequestMeta(
        request_id=uuid4(),
        correlation=Correlation(correlation_id=uuid4()),
        expected_revision=expected_revision,
    )


def record(session_id: UUID) -> HarnessSessionRecord:
    return HarnessSessionRecord(
        session_id=session_id,
        repository_id=uuid4(),
        harness="codex",
        transport=SessionTransport.TMUX,
        transport_ref="murder_test",
        status=SessionStatus.READY,
        revision=0,
        capabilities=SessionCapabilities(structured_messages=True),
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )


@pytest.fixture
def lease_store() -> tuple[SessionStore, Clock, UUID]:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    clock = Clock()
    store = SessionStore(connection, clock=clock)
    session_id = uuid4()
    store.save_session(record(session_id))
    return store, clock, session_id


def test_schema_setup_and_session_facts_preserve_owner_transaction() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    ensure_session_schema(connection)
    session_id = uuid4()

    connection.execute("BEGIN IMMEDIATE")
    ensure_session_schema(connection)
    SessionStore(connection).save_session(record(session_id))
    connection.rollback()

    assert connection.execute("SELECT COUNT(*) FROM harness_sessions").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM projection_inputs").fetchone()[0] == 0


def test_acquire_renew_release_and_fence_validation(
    lease_store: tuple[SessionStore, Clock, UUID],
) -> None:
    store, clock, session_id = lease_store
    holder = PrincipalRef(kind=PrincipalKind.USER, id="luke")
    granted = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=holder,
    )
    assert isinstance(granted, WriterLeaseGranted)
    assert granted.lease.fence == 1
    assert (
        store.validate_writer_lease(
            session_id=session_id,
            lease_id=granted.lease.lease_id,
            fence=1,
            holder=holder,
            required_mode=WriterMode.RAW_TERMINAL,
        )
        == granted.lease
    )

    clock.advance(2)
    renewed = store.renew_writer_lease(
        RenewWriterLease(
            meta=meta(),
            lease_id=granted.lease.lease_id,
            fence=1,
            ttl_seconds=30,
        ),
        holder=holder,
    )
    assert isinstance(renewed, WriterLeaseGranted)
    assert renewed.lease.renewed_at == clock.now
    assert renewed.lease.expires_at == clock.now + timedelta(seconds=30)

    released = store.release_writer_lease(
        ReleaseWriterLease(
            meta=meta(),
            lease_id=granted.lease.lease_id,
            fence=1,
        ),
        holder=holder,
    )
    assert isinstance(released, WriterLeaseGranted)
    assert released.lease.revoked_at == clock.now
    with pytest.raises(StaleWriterLeaseError):
        store.validate_writer_lease(
            session_id=session_id,
            lease_id=granted.lease.lease_id,
            fence=1,
            holder=holder,
        )


def test_force_takeover_revokes_old_holder_and_fences_late_write(
    lease_store: tuple[SessionStore, Clock, UUID],
) -> None:
    store, _, session_id = lease_store
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    workflow = PrincipalRef(kind=PrincipalKind.WORKFLOW, id="workflow-1")
    first = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(first, WriterLeaseGranted)

    denied = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.STRUCTURED,
            force=True,
        ),
        holder=workflow,
    )
    assert isinstance(denied, WriterLeaseDenied)
    assert "permission" in denied.reason

    takeover = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.STRUCTURED,
            force=True,
        ),
        holder=workflow,
        force_authorized=True,
        revocation_reason="approved takeover",
    )
    assert isinstance(takeover, WriterLeaseGranted)
    assert takeover.lease.fence == SECOND_FENCE
    audit_count = store._connection.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM writer_lease_audit_facts WHERE session_id = ?",
        (str(session_id),),
    ).fetchone()[0]
    assert audit_count == 1
    with pytest.raises(StaleWriterLeaseError):
        store.validate_writer_lease(
            session_id=session_id,
            lease_id=first.lease.lease_id,
            fence=first.lease.fence,
            holder=human,
        )


def test_expiry_allows_new_acquisition_but_never_reuses_fence(
    lease_store: tuple[SessionStore, Clock, UUID],
) -> None:
    store, clock, session_id = lease_store
    first_holder = PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal-1")
    second_holder = PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal-2")
    first = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
            ttl_seconds=3,
        ),
        holder=first_holder,
    )
    assert isinstance(first, WriterLeaseGranted)
    clock.advance(4)
    second = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=second_holder,
    )
    assert isinstance(second, WriterLeaseGranted)
    assert second.lease.fence == first.lease.fence + 1


def test_second_writer_is_denied_with_retry_information(
    lease_store: tuple[SessionStore, Clock, UUID],
) -> None:
    store, _, session_id = lease_store
    first_holder = PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal-1")
    first = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=first_holder,
    )
    assert isinstance(first, WriterLeaseGranted)
    denied = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=PrincipalRef(kind=PrincipalKind.CLIENT, id="terminal-2"),
    )
    assert isinstance(denied, WriterLeaseDenied)
    assert denied.current_holder == first_holder
    assert denied.retry_after == first.lease.expires_at
