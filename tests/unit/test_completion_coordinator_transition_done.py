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

from murder.state.persistence.connection import RepoDb
from murder.state.persistence.tickets import get_ticket_status
from murder.verdict.completion.coordinator import CompletionCoordinator
from murder.work.tickets.status import TicketStatus


def _db() -> RepoDb:
    from tests.support.database import open_test_repo_db

    return open_test_repo_db(Path(":memory:"))


def _insert_ticket(db: RepoDb, tid: str, status: str) -> None:
    db.conn.execute(
        "INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (db.repository_id, tid, f"Title {tid}", status),
    )


def _coordinator(db: RepoDb, monkeypatch):
    registry = MagicMock()
    coordinator = CompletionCoordinator(
        registry,
        repo_root=Path("/tmp"),
        db=db,
        events=None,
        run_id=None,
        find_crow=lambda _tid: None,
        find_agent=lambda _aid: None,
    )

    # Avoid touching the filesystem for worktree pruning. The done-path prune now
    # lives in TicketOutcomeService (call-site import of the worktree helper), so
    # patch the helper at its source rather than a coordinator method.
    async def _noop_prune(*_a, **_k):
        return None

    monkeypatch.setattr("murder.state.storage.worktrees.prune_terminal_crow_worktree", _noop_prune)
    return coordinator


def test_transition_done_from_ready_normalizes_through_in_progress(monkeypatch):
    db = _db()
    _insert_ticket(db, "t1", TicketStatus.READY.value)
    coord = _coordinator(db, monkeypatch)

    asyncio.run(coord._transition_done("t1"))

    assert get_ticket_status(db, "t1") == TicketStatus.DONE.value


def test_transition_done_from_in_progress_completes(monkeypatch):
    db = _db()
    _insert_ticket(db, "t2", TicketStatus.IN_PROGRESS.value)
    coord = _coordinator(db, monkeypatch)

    asyncio.run(coord._transition_done("t2"))

    assert get_ticket_status(db, "t2") == TicketStatus.DONE.value


def test_transition_done_already_done_is_noop(monkeypatch):
    db = _db()
    _insert_ticket(db, "t3", TicketStatus.DONE.value)
    coord = _coordinator(db, monkeypatch)

    # Must not raise InvalidTransition and must leave the ticket done.
    asyncio.run(coord._transition_done("t3"))

    assert get_ticket_status(db, "t3") == TicketStatus.DONE.value


def test_transition_done_from_blocked_normalizes(monkeypatch):
    db = _db()
    _insert_ticket(db, "t4", TicketStatus.BLOCKED.value)
    coord = _coordinator(db, monkeypatch)

    asyncio.run(coord._transition_done("t4"))

    assert get_ticket_status(db, "t4") == TicketStatus.DONE.value


def test_transition_done_skips_archived_terminal_state(monkeypatch):
    db = _db()
    _insert_ticket(db, "t5", TicketStatus.ARCHIVED.value)
    coord = _coordinator(db, monkeypatch)

    # Archived is not promotable to done; must not raise and must stay archived.
    asyncio.run(coord._transition_done("t5"))

    assert get_ticket_status(db, "t5") == TicketStatus.ARCHIVED.value


def test_transition_done_skips_failed_terminal_state(monkeypatch):
    db = _db()
    _insert_ticket(db, "t6", TicketStatus.FAILED.value)
    coord = _coordinator(db, monkeypatch)

    asyncio.run(coord._transition_done("t6"))

    assert get_ticket_status(db, "t6") == TicketStatus.FAILED.value
