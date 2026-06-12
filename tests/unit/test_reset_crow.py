"""reset_crow: one-step kill + re-queue for a stuck/wrong-track crow (Objective 1).

Replaces the manual retry_failed -> force_status -> kickoff_ready sequence:
works from in_progress, reaps crow + handler, kills any DB-recorded session,
and transitions the ticket to ready (not failed).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from murder.runtime.orchestration.ticket_ops import TicketOps
from murder.runtime.workers.orchestrator_worker import _HANDLERS
from murder.state.persistence.schema import get_db, init_db


def _db():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    return conn


def _insert_ticket(conn, tid: str, status: str = "in_progress", last_error: str | None = None):
    conn.execute(
        "INSERT INTO tickets(id, title, status, last_error, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (tid, f"Title {tid}", status, last_error),
    )


def _ops(conn):
    rt = MagicMock()
    rt.db = conn
    rt.reap = AsyncMock()
    emitted: list[tuple] = []

    async def _emit(ticket_id, frm, to):
        emitted.append((ticket_id, frm, to))

    return TicketOps(rt, emit_ticket_status=_emit), rt, emitted


def test_reset_crow_from_in_progress_reaps_and_readies():
    conn = _db()
    _insert_ticket(conn, "t001", "in_progress", last_error="boom")
    ops, rt, emitted = _ops(conn)

    result = asyncio.run(ops.reset_crow("t001"))

    assert result["ok"] is True
    assert result["prev_status"] == "in_progress"
    row = conn.execute("SELECT status, last_error FROM tickets WHERE id = 't001'").fetchone()
    assert row["status"] == "ready"
    assert row["last_error"] is None
    reaped = {c.args[0] for c in rt.reap.await_args_list}
    assert reaped == {"crow-t001", "crow_handler-t001"}
    assert len(emitted) == 1
    tid, frm, to = emitted[0]
    assert (tid, to) == ("t001", "ready")


def test_reset_crow_unknown_ticket_errors_cleanly():
    conn = _db()
    ops, rt, emitted = _ops(conn)

    result = asyncio.run(ops.reset_crow("t404"))

    assert result["ok"] is False
    assert "not found" in result["error"]
    rt.reap.assert_not_awaited()
    assert emitted == []


def test_reset_crow_kills_db_recorded_session_when_no_inmemory_agent(monkeypatch):
    """A crow from a previous process has no in-memory agent to reap; the
    session recorded in the agents table must still be killed and NULLed."""
    conn = _db()
    _insert_ticket(conn, "t002", "in_progress")
    conn.execute(
        "INSERT INTO agents(agent_id, role, ticket_id, status, session, started_at) "
        "VALUES ('crow-t002', 'crow', 't002', 'running', 'crow-t002-sess', '2026-01-01')"
    )
    ops, rt, _ = _ops(conn)

    killed: list[str] = []

    async def _kill(session: str) -> None:
        killed.append(session)

    from murder.runtime.terminal import tmux as tmux_mod

    monkeypatch.setattr(tmux_mod, "kill_session", _kill)

    result = asyncio.run(ops.reset_crow("t002"))

    assert result["ok"] is True
    assert killed == ["crow-t002-sess"]
    row = conn.execute("SELECT session FROM agents WHERE agent_id = 'crow-t002'").fetchone()
    assert row["session"] is None


def test_crow_reset_command_registered_and_validates():
    handler = _HANDLERS["crow.reset"]
    orch = MagicMock()
    orch.reset_crow = AsyncMock(return_value={"handled": True, "ok": True})

    result = asyncio.run(handler(orch, {"ticket_id": " t007 "}))
    assert result["handled"] is True
    orch.reset_crow.assert_awaited_once_with("t007")

    try:
        asyncio.run(handler(orch, {}))
    except ValueError as exc:
        assert "ticket_id" in str(exc)
    else:
        raise AssertionError("missing ticket_id must raise ValueError")
