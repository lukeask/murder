"""CompletionCoordinator._transition_done: normalize-then-complete (Item 3).

A reattach can observe `>>> DONE` against a ticket still in READY (the
ready->done lifecycle race). The coordinator must walk it up to in_progress
first rather than attempting an invalid raw READY -> DONE jump, treat an
already-done ticket as a no-op, and skip non-promotable terminal states.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from murder.verdict.completion.coordinator import CompletionCoordinator
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import get_ticket_status
from murder.work.tickets.status import TicketStatus


def _db():
    conn = get_db(Path(":memory:"))
    init_db(conn)
    return conn


def _insert_ticket(conn, tid: str, status: str) -> None:
    conn.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
        (tid, f"Title {tid}", status),
    )


def _coordinator(conn, monkeypatch):
    rt = MagicMock()
    rt.db = conn
    rt.repo_root = Path("/tmp")
    rt.bus = None
    rt.run_id = None
    registry = MagicMock()
    coordinator = CompletionCoordinator(rt, registry)
    # Avoid touching the filesystem for worktree pruning. The done-path prune now
    # lives in TicketOutcomeService (call-site import of the worktree helper), so
    # patch the helper at its source rather than a coordinator method.
    async def _noop_prune(*_a, **_k):
        return None

    monkeypatch.setattr(
        "murder.state.storage.worktrees.prune_terminal_crow_worktree", _noop_prune
    )
    return coordinator


def test_transition_done_from_ready_normalizes_through_in_progress(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t1", TicketStatus.READY.value)
    coord = _coordinator(conn, monkeypatch)

    asyncio.run(coord._transition_done("t1"))

    assert get_ticket_status(conn, "t1") == TicketStatus.DONE.value


def test_transition_done_from_in_progress_completes(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t2", TicketStatus.IN_PROGRESS.value)
    coord = _coordinator(conn, monkeypatch)

    asyncio.run(coord._transition_done("t2"))

    assert get_ticket_status(conn, "t2") == TicketStatus.DONE.value


def test_transition_done_already_done_is_noop(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t3", TicketStatus.DONE.value)
    coord = _coordinator(conn, monkeypatch)

    # Must not raise InvalidTransition and must leave the ticket done.
    asyncio.run(coord._transition_done("t3"))

    assert get_ticket_status(conn, "t3") == TicketStatus.DONE.value


def test_transition_done_from_blocked_normalizes(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t4", TicketStatus.BLOCKED.value)
    coord = _coordinator(conn, monkeypatch)

    asyncio.run(coord._transition_done("t4"))

    assert get_ticket_status(conn, "t4") == TicketStatus.DONE.value


def test_transition_done_skips_archived_terminal_state(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t5", TicketStatus.ARCHIVED.value)
    coord = _coordinator(conn, monkeypatch)

    # Archived is not promotable to done; must not raise and must stay archived.
    asyncio.run(coord._transition_done("t5"))

    assert get_ticket_status(conn, "t5") == TicketStatus.ARCHIVED.value


def test_transition_done_skips_failed_terminal_state(monkeypatch):
    conn = _db()
    _insert_ticket(conn, "t6", TicketStatus.FAILED.value)
    coord = _coordinator(conn, monkeypatch)

    asyncio.run(coord._transition_done("t6"))

    assert get_ticket_status(conn, "t6") == TicketStatus.FAILED.value
