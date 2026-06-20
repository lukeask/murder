"""CrowHandler DONE latch — completion is robust to the marker scrolling out.

Regression for the dogfood bug: a crow emitted ``>>> DONE`` but the ticket
parked at ``in_progress`` because the marker scrolled out of the captured pane
window before a hash-changed beat fired, so the old hash-gated ``detect_done``
check never tripped. The fix latches the observed DONE and drives completion
off the latch, so once the marker is seen on ANY beat completion fires exactly
once even if a later capture no longer contains the marker.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.bus import Bus
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.orchestration.outcome import TicketOutcomeService
from murder.verdict.completion.coordinator import CompletionCoordinator
from murder.state.persistence.schema import get_db, init_db
from murder.work.tickets.status import TicketStatus

# A claude_code assistant turn ending in a standalone `>>> DONE` line. The
# transcript parser projects this pane into an assistant segment whose text the
# `detect_done` source-aware scan inspects.
PANE_WITH_DONE = """\
> implement the thing

⏺ I implemented the thing and verified it builds.

>>> DONE
"""

# A later capture where the DONE block has scrolled out of the window entirely
# (the crow's pane now shows only fresh, unrelated tail output).
PANE_DONE_SCROLLED_OUT = """\
> implement the thing

⏺ Wrapping up; here is some trailing output that pushed DONE off-screen.
  line a
  line b
  line c
"""


@pytest.fixture
def db(tmp_path: Path):
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)
    yield conn
    conn.close()


def _seed_ticket(db, status: str = "in_progress") -> None:
    db.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) "
        "VALUES ('test-run', '2026-01-01', '{}')"
    )
    db.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at) "
        "VALUES ('t001', 'Wire up the thing', ?, '2026-01-01', '2026-01-01')",
        (status,),
    )


def _make_handler(db, tmp_path: Path, coordinator) -> CrowHandler:
    runtime = MagicMock()
    runtime.db = db
    runtime.bus = Bus(run_id="test-run", db_conn=db)
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


def _real_coordinator(db, tmp_path: Path) -> CompletionCoordinator:
    """A coordinator whose registry assigns NO checks → completion transitions done."""
    rt = MagicMock()
    rt.db = db
    rt.repo_root = tmp_path
    rt.bus = Bus(run_id="test-run", db_conn=db)
    rt.run_id = "test-run"
    rt.publish_snapshot = AsyncMock()
    rt.get_crow = MagicMock(return_value=None)
    registry = MagicMock()
    registry.assigned_checks = MagicMock(return_value=[])
    return CompletionCoordinator(rt, registry)


def _ticket_status(db) -> str | None:
    from murder.state.persistence.tickets import get_ticket_status

    return get_ticket_status(db, "t001")


def test_done_in_prior_beat_still_completes_when_marker_scrolls_out(db, fake_tmux, tmp_path):
    """The marker is visible on beat 1, gone on beat 2; completion fires once."""
    _seed_ticket(db)
    coordinator = _real_coordinator(db, tmp_path)
    handler = _make_handler(db, tmp_path, coordinator)

    # Beat 1: DONE is visible → latch + complete → ticket transitions to done.
    fake_tmux.queue_pane(PANE_WITH_DONE)
    asyncio.run(handler.tick())

    assert handler._done_latched is True
    assert handler._completion_succeeded is True
    assert _ticket_status(db) == TicketStatus.DONE.value

    # Beat 2: the marker has scrolled out, but completion already succeeded and
    # must NOT re-run. (Latch stays terminal; no second handle_done.)
    fake_tmux.reset_queue()
    fake_tmux.queue_pane(PANE_DONE_SCROLLED_OUT)
    asyncio.run(handler.tick())
    assert _ticket_status(db) == TicketStatus.DONE.value


def test_completion_fires_exactly_once_across_repeated_ticks(db, fake_tmux, tmp_path):
    """Even with the marker visible on every beat, handle_done runs once."""
    _seed_ticket(db)
    coordinator = _real_coordinator(db, tmp_path)
    spy = AsyncMock(wraps=coordinator.handle_done)
    coordinator.handle_done = spy  # type: ignore[method-assign]
    handler = _make_handler(db, tmp_path, coordinator)

    for _ in range(4):
        fake_tmux.reset_queue()
        fake_tmux.queue_pane(PANE_WITH_DONE)
        asyncio.run(handler.tick())

    spy.assert_awaited_once()
    assert _ticket_status(db) == TicketStatus.DONE.value


def test_latch_set_even_on_idle_hash_stable_beat(db, fake_tmux, tmp_path):
    """A DONE arriving on an idle / unchanged-pane beat must still complete.

    The old gate skipped a DONE that coincided with a hash-stable beat; the
    latch path is intentionally not gated on hash change or idle state.
    """
    _seed_ticket(db)
    coordinator = _real_coordinator(db, tmp_path)
    handler = _make_handler(db, tmp_path, coordinator)
    # Force the handler to believe the pane is idle (claude_code idle detection
    # is irrelevant to the latch path; we assert the latch fires regardless).
    handler._idle_cached = True

    fake_tmux.queue_pane(PANE_WITH_DONE)
    asyncio.run(handler.tick())

    assert handler._done_latched is True
    assert _ticket_status(db) == TicketStatus.DONE.value
