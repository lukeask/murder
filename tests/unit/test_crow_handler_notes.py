"""CrowHandler routes verified ``>>> ASK`` markers directly to orchestration.

Regression guard for review finding #7: the legacy ``ticket_parser.append_section``
sink rewrote only ``## Plan`` / ``## Working notes`` and dropped frontmatter +
``# Checklist``, so a note could clobber unified-ticket metadata. Under
NOTE markers are neither public facts nor generic orchestration events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.orchestration.outcome import TicketOutcomeService
from murder.state.persistence.conversation import project_parsed_doc_with_changes
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import ticket_md

SESSION = "crow-t001"

# A unified ticket: YAML frontmatter + a body ``# Checklist`` heading. The legacy
# append_section() would have destroyed both of these.
UNIFIED_TICKET_MD = """\
---
id: t001
title: Wire up the thing
status: in_progress
harness: claude_code
---

# Checklist

- [ ] do the thing
- [ ] verify the thing
"""

PANE_WITH_NOTE = ">>> NOTE: discovered the config lives in settings.json\n>>> END\n"


@pytest.fixture
def db(tmp_path: Path):
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def handler(db, tmp_path: Path) -> CrowHandler:
    runtime = MagicMock()
    runtime.db = db
    runtime.orchestration_events = InProcessOrchestrationEventSink()
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
    runtime.publish_snapshot = AsyncMock()
    runtime.crow_ask_router = None
    return CrowHandler(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="handler-log",
        crow_session=SESSION,
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=0.0),
        repo_root=tmp_path,
        runtime=runtime,
        outcome=MagicMock(spec=TicketOutcomeService),
        coordinator=MagicMock(),
    )


def _seed_ticket(db, repo_root: Path) -> Path:
    db.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) "
        "VALUES ('test-run', '2026-01-01', '{}')"
    )
    db.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at) "
        "VALUES ('t001', 'Wire up the thing', 'in_progress', '2026-01-01', '2026-01-01')"
    )
    tpath = ticket_md(repo_root, "t001")
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(UNIFIED_TICKET_MD, encoding="utf-8")
    return tpath


def test_note_leaves_unified_ticket_untouched(handler, db, tmp_path):
    tpath = _seed_ticket(db, tmp_path)
    before = tpath.read_bytes()

    _, changes = project_parsed_doc_with_changes(
        db,
        "crow-t001",
        {
            "harness": "claude_code",
            "state": "awaiting_input",
            "segments": [{"type": "assistant", "phase": "final", "text": PANE_WITH_NOTE}],
        },
    )
    asyncio.run(handler.observe_conversation_changes(changes))

    # ...and the unified ticket .md — frontmatter + checklist — is byte-for-byte intact.
    assert tpath.read_bytes() == before


def test_projected_assistant_messages_emit_markers_once_each(handler, db, tmp_path) -> None:
    """Stable frames replay nothing; a distinct assistant block remains distinct."""
    _seed_ticket(db, tmp_path)
    routed = AsyncMock()
    handler.runtime.crow_ask_router = routed
    marker = ">>> ASK: which port should I use?\n>>> DONE"
    first_doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "segments": [{"type": "assistant", "phase": "final", "text": marker}],
    }
    _, first = project_parsed_doc_with_changes(db, "crow-t001", first_doc)
    asyncio.run(handler.observe_conversation_changes(first))
    _, unchanged = project_parsed_doc_with_changes(db, "crow-t001", first_doc)
    asyncio.run(handler.observe_conversation_changes(unchanged))

    second_doc = {
        **first_doc,
        "segments": [
            *first_doc["segments"],
            {"type": "assistant", "phase": "final", "text": marker},
        ],
    }
    _, second = project_parsed_doc_with_changes(db, "crow-t001", second_doc)
    asyncio.run(handler.observe_conversation_changes(second))

    assert routed.await_count == 2


def test_growing_assistant_message_emits_each_completed_marker_once(handler, db, tmp_path) -> None:
    _seed_ticket(db, tmp_path)
    routed = AsyncMock()
    handler.runtime.crow_ask_router = routed
    first_text = ">>> ASK: first question\n>>> NOTE: first note\n>>> END"
    working_doc = {
        "harness": "claude_code",
        "state": "working",
        "segments": [{"type": "assistant", "phase": "intermediate", "text": first_text}],
    }
    _, first = project_parsed_doc_with_changes(db, "crow-t001", working_doc)
    asyncio.run(handler.observe_conversation_changes(first))
    _, unchanged = project_parsed_doc_with_changes(db, "crow-t001", working_doc)
    asyncio.run(handler.observe_conversation_changes(unchanged))

    grown_text = first_text + "\n>>> ASK: second question\n>>> DONE"
    grown_doc = {
        **working_doc,
        "segments": [{"type": "assistant", "phase": "intermediate", "text": grown_text}],
    }
    _, grown = project_parsed_doc_with_changes(db, "crow-t001", grown_doc)
    asyncio.run(handler.observe_conversation_changes(grown))
    sealed_doc = {
        **grown_doc,
        "state": "awaiting_input",
        "segments": [{"type": "assistant", "phase": "final", "text": grown_text}],
    }
    _, sealed = project_parsed_doc_with_changes(db, "crow-t001", sealed_doc)
    asyncio.run(handler.observe_conversation_changes(sealed))

    assert routed.await_count == 2
