"""/resume from history: spawn a fresh CC crow with ``--resume <session_id>``.

Covers ``Orchestrator.resume_conversation`` (the validation + spawn) and the
``agent.resume_from_history`` worker handler dispatch. CC-only: a non-CC
conversation, a missing session id, or a missing conversation each return an
error dict (never raise); a valid completed CC conversation calls ``spawn_rogue``
with the captured session id; an already-live crow short-circuits.

Convention: ``asyncio.run()`` for async (no ``@pytest.mark.asyncio``). We drive
the orchestrator method / worker handler directly and never start the runtime.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.workers.orchestrator_worker import _HANDLERS
from murder.state.persistence import conversation
from murder.state.persistence.schema import get_db, init_db


def _db() -> sqlite3.Connection:
    conn = get_db(Path(":memory:"))
    init_db(conn)
    return conn


def _add_conversation(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    harness: str | None,
    harness_session_id: str | None,
    status: str,
) -> None:
    """Append a user turn then stamp harness/session/status (graceful-exit order)."""
    conversation.append_user_message(conn, conversation_id, "do the thing")
    conversation.upsert_conversation(
        conn,
        conversation_id=conversation_id,
        agent_id=conversation_id,
        harness=harness,
        harness_session_id=harness_session_id,
    )
    if status != "in_progress":
        conversation.set_conversation_status(conn, conversation_id, status)
    conn.commit()


def _orchestrator(conn: sqlite3.Connection) -> tuple[Orchestrator, MagicMock, list[dict[str, Any]]]:
    rt = MagicMock()
    rt.db = conn
    rt.get_agent = MagicMock(return_value=None)
    orch = Orchestrator(rt)

    spawned: list[dict[str, Any]] = []

    async def _fake_spawn_rogue(harness: str, model: str, *args: Any, **kwargs: Any) -> str:
        spawned.append({"harness": harness, "model": model, "args": args, **kwargs})
        return "crow-rogue-resumed"

    orch.spawn_rogue = _fake_spawn_rogue  # type: ignore[assignment]
    # _agent_is_live is only consulted when get_agent returns a live handle.
    orch._agent_is_live = AsyncMock(return_value=True)  # type: ignore[assignment]
    return orch, rt, spawned


def test_resume_valid_cc_conversation_spawns_with_session_id() -> None:
    conn = _db()
    _add_conversation(
        conn,
        "crow-t1",
        harness="claude_code",
        harness_session_id="sess-abc",
        status="complete",
    )
    orch, _rt, spawned = _orchestrator(conn)

    result = asyncio.run(orch.resume_conversation("crow-t1"))

    assert result["handled"] is True
    assert result["resumed_from"] == "crow-t1"
    assert result["agent_id"] == "crow-rogue-resumed"
    assert len(spawned) == 1
    assert spawned[0]["harness"] == "claude_code"
    assert spawned[0]["resume_session_id"] == "sess-abc"


def test_resume_non_cc_conversation_returns_error() -> None:
    conn = _db()
    _add_conversation(
        conn,
        "crow-cursor",
        harness="cursor",
        harness_session_id="sess-ghi",
        status="complete",
    )
    orch, _rt, spawned = _orchestrator(conn)

    result = asyncio.run(orch.resume_conversation("crow-cursor"))

    assert result["ok"] is False
    assert "Claude Code" in result["error"]
    assert spawned == []


def test_resume_missing_session_id_returns_error() -> None:
    conn = _db()
    _add_conversation(
        conn,
        "crow-nosess",
        harness="claude_code",
        harness_session_id=None,
        status="complete",
    )
    orch, _rt, spawned = _orchestrator(conn)

    result = asyncio.run(orch.resume_conversation("crow-nosess"))

    assert result["ok"] is False
    assert "session id" in result["error"]
    assert spawned == []


def test_resume_unknown_conversation_returns_error() -> None:
    conn = _db()
    orch, _rt, spawned = _orchestrator(conn)

    result = asyncio.run(orch.resume_conversation("crow-missing"))

    assert result["ok"] is False
    assert "no conversation" in result["error"]
    assert spawned == []


def test_resume_already_running_crow_short_circuits() -> None:
    conn = _db()
    _add_conversation(
        conn,
        "crow-live",
        harness="claude_code",
        harness_session_id="sess-abc",
        status="complete",
    )
    orch, rt, spawned = _orchestrator(conn)
    # A live in-memory crow for this conversation: resume must not fork a copy.
    rt.get_agent = MagicMock(return_value=MagicMock())
    orch._agent_is_live = AsyncMock(return_value=True)  # type: ignore[assignment]

    result = asyncio.run(orch.resume_conversation("crow-live"))

    assert result["ok"] is False
    assert "already running" in result["error"]
    assert spawned == []


def test_handler_requires_conversation_id() -> None:
    handler = _HANDLERS["agent.resume_from_history"]
    orch = MagicMock()
    try:
        asyncio.run(handler(orch, {}))
    except ValueError as exc:
        assert "conversation_id" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError for missing conversation_id")


def test_handler_dispatches_to_resume_conversation() -> None:
    handler = _HANDLERS["agent.resume_from_history"]
    orch = MagicMock()
    orch.resume_conversation = AsyncMock(return_value={"handled": True})

    result = asyncio.run(handler(orch, {"conversation_id": " crow-t1 "}))

    assert result == {"handled": True}
    orch.resume_conversation.assert_awaited_once_with("crow-t1")
