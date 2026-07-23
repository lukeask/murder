"""CrowHandler promotability guard (Item 3).

On reattach the DONE latch can fire off a `>>> DONE` left in scrollback while
the ticket has already moved to a terminal state (done/failed/archived). The
handler must skip completion for a non-promotable ticket rather than driving an
invalid transition / re-firing checks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.bus import OrchestrationNotifier
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.orchestration.outcome import TicketOutcomeService
from murder.verdict.completion.coordinator import CompletionCoordinator
from murder.state.persistence.schema import get_db, init_db
from murder.work.tickets.status import TicketStatus

PANE_WITH_DONE = """\
> implement the thing

⏺ I implemented the thing and verified it builds.

>>> DONE
"""


@pytest.fixture
def db(tmp_path: Path):
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)
    yield conn
    conn.close()


def _seed_ticket(db, status: str) -> None:
    db.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) "
        "VALUES ('test-run', '2026-01-01', '{}')"
    )
    db.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at) "
        "VALUES ('t001', 'Wire up the thing', ?, '2026-01-01', '2026-01-01')",
        (status,),
    )


def _coordinator(db, tmp_path: Path) -> CompletionCoordinator:
    rt = MagicMock()
    rt.db = db
    rt.repo_root = tmp_path
    rt.bus = OrchestrationNotifier(run_id="test-run", db_conn=db)
    rt.run_id = "test-run"
    rt.publish_snapshot = AsyncMock()
    rt.get_crow = MagicMock(return_value=None)
    registry = MagicMock()
    registry.assigned_checks = MagicMock(return_value=[])
    return CompletionCoordinator(rt, registry)


def _make_handler(db, tmp_path: Path, coordinator) -> CrowHandler:
    runtime = MagicMock()
    runtime.db = db
    runtime.bus = OrchestrationNotifier(run_id="test-run", db_conn=db)
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    runtime.publish_snapshot = AsyncMock()
    return CrowHandler(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="handler-log",
        crow_session="crow-t001",
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=0.0),
        repo_root=tmp_path,
        runtime=runtime,
        outcome=MagicMock(spec=TicketOutcomeService),
        coordinator=coordinator,
    )


def _status(db) -> str:
    from murder.state.persistence.tickets import get_ticket_status

    return get_ticket_status(db, "t001")


def test_promotable_when_in_progress(db, tmp_path):
    _seed_ticket(db, TicketStatus.IN_PROGRESS.value)
    handler = _make_handler(db, tmp_path, _coordinator(db, tmp_path))
    assert handler._ticket_promotable_to_done() is True


def test_not_promotable_when_already_done(db, tmp_path):
    _seed_ticket(db, TicketStatus.DONE.value)
    handler = _make_handler(db, tmp_path, _coordinator(db, tmp_path))
    assert handler._ticket_promotable_to_done() is False


def test_not_promotable_when_failed(db, tmp_path):
    _seed_ticket(db, TicketStatus.FAILED.value)
    handler = _make_handler(db, tmp_path, _coordinator(db, tmp_path))
    assert handler._ticket_promotable_to_done() is False


def test_not_promotable_when_archived(db, tmp_path):
    _seed_ticket(db, TicketStatus.ARCHIVED.value)
    handler = _make_handler(db, tmp_path, _coordinator(db, tmp_path))
    assert handler._ticket_promotable_to_done() is False


def test_maybe_complete_skips_completion_on_terminal_ticket(db, fake_tmux, tmp_path):
    """Scrollback DONE against an already-failed ticket must not run completion."""
    _seed_ticket(db, TicketStatus.FAILED.value)
    coordinator = _coordinator(db, tmp_path)
    spy = AsyncMock(wraps=coordinator.handle_done)
    coordinator.handle_done = spy  # type: ignore[method-assign]
    handler = _make_handler(db, tmp_path, coordinator)

    # Drive a tick that observes DONE in scrollback.
    fake_tmux.queue_pane(PANE_WITH_DONE)
    asyncio.run(handler.tick())

    # Completion was skipped: handle_done never ran, ticket stays failed, and the
    # latch was cleared so we don't spin re-firing against a terminal ticket.
    spy.assert_not_awaited()
    assert _status(db) == TicketStatus.FAILED.value
    assert handler._done_latched is False
