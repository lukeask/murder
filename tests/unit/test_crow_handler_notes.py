"""CrowHandler routes ``>>> NOTE`` output to the DB events table, not the ticket .md.

Regression guard for review finding #7: the legacy ``ticket_parser.append_section``
sink rewrote only ``## Plan`` / ``## Working notes`` and dropped frontmatter +
``# Checklist``, so a note could clobber unified-ticket metadata. Under
DB-owns-runtime notes now land in the ``events`` table via the bus.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from murder.bus import Bus
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.orchestration.outcome import TicketOutcomeService
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
    runtime.bus = Bus(run_id="test-run", db_conn=db)
    runtime.run_id = "test-run"
    runtime.sync_agent = MagicMock()
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


def test_note_lands_in_db_and_leaves_unified_ticket_untouched(handler, db, tmp_path):
    tpath = _seed_ticket(db, tmp_path)
    before = tpath.read_bytes()

    asyncio.run(handler._orchestration_tick(PANE_WITH_NOTE))

    # The note is durable in the events audit log...
    rows = db.execute(
        "SELECT type, ticket_id, payload_json FROM events WHERE type = 'note'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["ticket_id"] == "t001"
    assert "discovered the config lives in settings.json" in rows[0]["payload_json"]

    # ...and the unified ticket .md — frontmatter + checklist — is byte-for-byte intact.
    assert tpath.read_bytes() == before
