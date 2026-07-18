"""Startup reattach: live crows on in_progress tickets are rehydrated, not failed.

See recovery.reconcile_agents_vs_tmux (Objective 2). A crow whose tmux session
survived a restart must be queued for reattach (so DONE is eventually consumed),
the ticket must stay in_progress, and the stale handler row must be cleaned up.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from murder.app.service.recovery import ReconcileReport, reconcile_agents_vs_tmux
from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    PrincipalKind,
    PrincipalRef,
    RequestMeta,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLeaseGranted,
    WriterMode,
)
from murder.runtime.sessions.persistence import SessionStore
from murder.state.persistence.schema import get_db, init_db


def _db():
    conn = get_db(__import__("pathlib").Path(":memory:"))
    init_db(conn)
    return conn


def _insert_ticket(conn, tid: str, status: str = "in_progress") -> None:
    conn.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
        (tid, f"Title {tid}", status),
    )


def _insert_agent(
    conn,
    agent_id: str,
    role: str,
    status: str,
    session: str | None,
    ticket_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO agents(agent_id, role, ticket_id, status, session, started_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-01-01')",
        (agent_id, role, ticket_id, status, session),
    )


def test_live_crow_queued_for_reattach_ticket_stays_in_progress():
    conn = _db()
    _insert_ticket(conn, "t001")
    _insert_agent(conn, "crow-t001", "crow", "running", "crow-t001", "t001")

    report = reconcile_agents_vs_tmux(conn, live_sessions={"crow-t001"})

    assert ("t001", "crow-t001") in report.crows_to_reattach
    assert "crow-t001" not in report.agents_marked_dead
    assert "t001" not in report.tickets_reset_to_failed
    status = conn.execute("SELECT status FROM tickets WHERE id = 't001'").fetchone()["status"]
    assert status == "in_progress"
    crow_status = conn.execute(
        "SELECT status FROM agents WHERE agent_id = 'crow-t001'"
    ).fetchone()["status"]
    assert crow_status == "running"


def test_dead_crow_session_gone_fails_ticket_no_reattach():
    conn = _db()
    _insert_ticket(conn, "t002")
    _insert_agent(conn, "crow-t002", "crow", "running", "crow-t002", "t002")

    # Session NOT in live_sessions → existing zombie behavior.
    report = reconcile_agents_vs_tmux(conn, live_sessions=set())

    assert report.crows_to_reattach == []
    assert "crow-t002" in report.agents_marked_dead
    assert "t002" in report.tickets_reset_to_failed
    status = conn.execute("SELECT status FROM tickets WHERE id = 't002'").fetchone()["status"]
    assert status == "failed"


def test_stale_handler_row_marked_dead_and_session_queued():
    conn = _db()
    _insert_ticket(conn, "t003")
    _insert_agent(conn, "crow-t003", "crow", "running", "crow-t003", "t003")
    # Stale handler row, non-terminal, whose debug log-tail session is still
    # live (in live_sessions) so the first loop leaves it alone; the reattach
    # second pass is what marks it dead and queues its session for killing.
    _insert_agent(conn, "crow_handler-t003", "crow_handler", "running", "handler-log-t003", "t003")

    report = reconcile_agents_vs_tmux(
        conn, live_sessions={"crow-t003", "handler-log-t003"}
    )

    assert ("t003", "crow-t003") in report.crows_to_reattach
    assert "crow_handler-t003" in report.agents_marked_dead
    assert "handler-log-t003" in report.sessions_to_kill
    handler_status = conn.execute(
        "SELECT status FROM agents WHERE agent_id = 'crow_handler-t003'"
    ).fetchone()["status"]
    assert handler_status == "dead"


def test_stale_handler_already_terminal_is_idempotent():
    conn = _db()
    _insert_ticket(conn, "t004")
    _insert_agent(conn, "crow-t004", "crow", "running", "crow-t004", "t004")
    # Handler already dead (e.g. first loop NULL-session path) — no double append.
    _insert_agent(conn, "crow_handler-t004", "crow_handler", "dead", None, "t004")

    report = reconcile_agents_vs_tmux(conn, live_sessions={"crow-t004"})

    assert ("t004", "crow-t004") in report.crows_to_reattach
    assert report.agents_marked_dead.count("crow_handler-t004") == 0


def test_missing_tmux_controller_session_is_lost_and_writer_revoked():
    conn = _db()
    store = SessionStore(conn)
    session_id = uuid4()
    store.save_session(
        HarnessSessionRecord(
            session_id=session_id,
            repository_id=uuid4(),
            harness="codex",
            transport=SessionTransport.TMUX,
            transport_ref="missing-pane",
            status=SessionStatus.READY,
            revision=4,
            capabilities=SessionCapabilities(raw_terminal=True),
            started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
        )
    )
    principal = PrincipalRef(kind=PrincipalKind.CLIENT, id="web")
    granted = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=RequestMeta(
                request_id=uuid4(),
                correlation=Correlation(correlation_id=uuid4()),
            ),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=principal,
    )
    assert isinstance(granted, WriterLeaseGranted)

    report = reconcile_agents_vs_tmux(conn, live_sessions=set())

    recovered = store.get_session(session_id)
    assert recovered is not None
    assert recovered.status is SessionStatus.LOST
    assert recovered.revision == 5
    assert store.active_writer_lease(session_id) is None
    assert report.harness_sessions_marked_lost == [str(session_id)]


def test_orchestrator_reattach_binds_live_session_without_prompt(fake_tmux, tmp_path):
    conn = _db()
    _insert_ticket(conn, "t100")

    rt = MagicMock()
    rt.repo_root = tmp_path
    rt.db = conn
    rt.config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt.register_agent = MagicMock()
    rt.sync_agent = MagicMock()
    rt.publish_snapshot = AsyncMock()
    rt.get_crow = MagicMock(return_value=None)
    # On a real restart the in-memory handler died, so the double-claim guard
    # sees no live handler and the ticket is still in_progress — reattach proceeds.
    rt.get_crow_handler = MagicMock(return_value=None)

    orch = Orchestrator(rt)
    # Spy on handler spawn so we don't drive the real handler coroutine.
    orch.spawn_crow_handler = AsyncMock(return_value="crow_handler-t100")

    asyncio.run(orch.reattach_crow("t100", "crow-t100"))

    # A CrowAgent was registered, bound to the live session, set RUNNING.
    assert rt.register_agent.call_count == 1
    agent = rt.register_agent.call_args[0][0]
    assert agent.id == "crow-t100"
    assert agent.session == "crow-t100"
    # No prompt: CrowAgent.start (which sends the brief) was never invoked, so the
    # fake tmux pane received no send_prompt. Handler was spawned with the session.
    orch.spawn_crow_handler.assert_awaited_once_with("t100", "crow-t100")
    rt.publish_snapshot.assert_awaited()


def test_reattach_skips_when_handler_already_live(fake_tmux, tmp_path):
    """Double-claim guard: if kickoff already spawned a live handler, reattach
    must not bind a duplicate CrowAgent against the same pane."""
    conn = _db()
    _insert_ticket(conn, "t200")  # in_progress

    rt = MagicMock()
    rt.repo_root = tmp_path
    rt.db = conn
    rt.config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt.register_agent = MagicMock()
    rt.sync_agent = MagicMock()
    rt.publish_snapshot = AsyncMock()
    rt.get_crow = MagicMock(return_value=None)
    rt.get_crow_handler = MagicMock(return_value=object())  # handler already live

    orch = Orchestrator(rt)
    orch.spawn_crow_handler = AsyncMock(return_value="crow_handler-t200")

    asyncio.run(orch.reattach_crow("t200", "crow-t200"))

    # Bailed: no duplicate agent registered, no second handler spawned.
    assert rt.register_agent.call_count == 0
    orch.spawn_crow_handler.assert_not_awaited()


def test_reattach_skips_when_ticket_not_in_progress(fake_tmux, tmp_path):
    """If kickoff moved the ticket out of in_progress (e.g. failed/done), the
    reattach claim is stale and must bail."""
    conn = _db()
    _insert_ticket(conn, "t201", status="failed")

    rt = MagicMock()
    rt.repo_root = tmp_path
    rt.db = conn
    rt.config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    rt.register_agent = MagicMock()
    rt.publish_snapshot = AsyncMock()
    rt.get_crow = MagicMock(return_value=None)
    rt.get_crow_handler = MagicMock(return_value=None)

    orch = Orchestrator(rt)
    orch.spawn_crow_handler = AsyncMock(return_value="crow_handler-t201")

    asyncio.run(orch.reattach_crow("t201", "crow-t201"))

    assert rt.register_agent.call_count == 0
    orch.spawn_crow_handler.assert_not_awaited()


def test_summary_and_bool_reflect_reattach_candidates():
    report = ReconcileReport(crows_to_reattach=[("t009", "crow-t009")])
    assert bool(report) is True
    assert "crows to reattach: t009(crow-t009)" in report.summary()

    empty = ReconcileReport()
    assert bool(empty) is False
    assert empty.summary() == "nothing to reconcile"
