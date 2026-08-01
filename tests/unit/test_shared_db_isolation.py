"""Regression coverage for repository partitions in the consolidated database."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

from murder.facts.contracts import (
    FactActor,
    FactCorrelation,
    PrivateFactPayload,
    RetainedFactDraft,
)
from murder.facts.log import append_fact, replay_facts
from murder.permissions import (
    LocalServicePermissionPolicy,
    PermissionPrincipal,
    PermissionService,
    PermissionStore,
    TerminalWrite,
)
from murder.runtime.sessions.contracts import (
    HarnessSessionRecord,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
)
from murder.runtime.sessions.persistence import SessionStore
from murder.state.persistence.connection import RepoDb


def _fact() -> RetainedFactDraft:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return RetainedFactDraft(
        fact_id=uuid4(),
        occurred_at=now,
        actor=FactActor(kind="service", id="isolation-test"),
        correlation=FactCorrelation(correlation_id=uuid4()),
        payload=PrivateFactPayload(kind="isolation.seed", data={"partition": "second"}),
    )


def test_fact_session_and_permission_stores_do_not_leak_partitions(
    repo_db: RepoDb, second_repo_db: RepoDb
) -> None:
    """Distinct UUID rows in the same file are invisible across repositories."""
    append_fact(second_repo_db, _fact())
    assert replay_facts(repo_db) == ()
    assert len(replay_facts(second_repo_db)) == 1

    session = HarnessSessionRecord(
        session_id=uuid4(),
        repository_id=UUID(second_repo_db.repository_id),
        harness="codex",
        transport=SessionTransport.TMUX,
        transport_ref="isolation-session",
        status=SessionStatus.READY,
        revision=0,
        capabilities=SessionCapabilities(structured_messages=True),
        started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    second_sessions = SessionStore(second_repo_db)
    second_sessions.save_session(session)
    assert SessionStore(repo_db).get_session(session.session_id) is None
    assert SessionStore(second_repo_db).get_session(session.session_id) == session

    operation = TerminalWrite(
        operation_id=uuid4(),
        principal=PermissionPrincipal(kind="service", id="isolation-test"),
        session_id=uuid4(),
        encoding="utf-8",
        data_digest=hashlib.sha256(b"isolation").hexdigest(),
        byte_count=9,
        lease_id=uuid4(),
        lease_fence=1,
    )
    service = PermissionService(
        store=PermissionStore(second_repo_db),
        policy=LocalServicePermissionPolicy(),
        clock=lambda: datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    service.request(operation)
    assert PermissionStore(repo_db).count("permission_policy_decisions") == 0
    assert PermissionStore(second_repo_db).count("permission_policy_decisions") == 1
