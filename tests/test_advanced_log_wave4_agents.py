"""AgentRuntime lifecycle capture rides the ONE bus aspect (plan §2.5.A).

AgentRuntime mutations schedule ``AgentLifecycleEvent`` on the bus → the recorder
SUBSCRIBER routes it into ``agent_records``. ``reap`` emits NO lifecycle event.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from murder.observability.advanced_log import (
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.observability.log_context import set_run_id
from murder.roster.service import RosterService
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.agents.events import AgentDoneEvent, LoggingAgentEventSink
from murder.runtime.agents.types import AgentRole, AgentStatus
from murder.runtime.agents.verified_control import VerifiedControlFactory
from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.state.storage.paths import db_path
from tests.support.database import open_test_repo_db


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def _rows(db_path: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


class _FakeAgent:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.role = AgentRole.CROW
        self.ticket_id = "T-1"
        self.session = f"sess-{agent_id}"
        self.status = AgentStatus.RUNNING
        self.harness = SimpleNamespace(kind="codex")
        self.startup_model = None
        self.start_commit = None
        self.worktree_path = None

    async def stop(self, *, failed: bool = True, kill_session: bool = True) -> None:
        del failed, kill_session
        return None


def test_agent_runtime_lifecycle_rides_bus_into_agent_records(tmp_path):
    repo = _repo(tmp_path)
    database = db_path(repo)
    database.parent.mkdir(parents=True, exist_ok=True)
    db = open_test_repo_db(database)
    db.conn.execute(
        "INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at) "
        "VALUES (?, 'T-1', 't', 'in_progress', '2026-01-01', '2026-01-01')",
        (db.repository_id,),
    )

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-w4", "redacted")
        await log.start()
        set_current_advanced_log(log)
        set_run_id("run-w4")
        bus = InProcessOrchestrationEventSink()

        async def _recorder(event):
            log.record_orchestration_event(event)

        bus.subscribe(_recorder)

        try:
            agents = AgentRuntime(
                db=db,
                roster=RosterService(db),
                events=bus,
                run_id="run-w4",
                advanced_log=log,
                preserve_tmux_on_close=lambda: False,
                verified_control_factory=VerifiedControlFactory(db=db),
                lifecycle_events_enabled=True,
            )
            agents.register(_FakeAgent("a-1"))
            agents.rename("a-1", "a-2")
            await agents.reap("a-2")  # NO AgentLifecycleEvent by design
            await agents.close()
            # Drain emission tasks scheduled during register/rename.
            await asyncio.sleep(0.05)
            await log.stop()
            return log._db_path
        finally:
            set_current_advanced_log(NullAdvancedLog())

    db_path_result = asyncio.run(_run())

    rows = _rows(db_path_result, "agent_records")
    payloads = [json.loads(r["payload"]) for r in rows]
    ops = [p["op"] for p in payloads]
    # register / rename ride the bus; reap/clear do not emit AgentLifecycleEvent.
    assert "register" in ops
    assert "rename" in ops
    assert "reap" not in ops
    assert all(r["run_id"] == "run-w4" for r in rows)


def test_agent_sink_does_not_double_write_to_recorder(tmp_path):
    """The Step 1.6 sink rides the Phase 1 log only — no agent_records row."""
    repo = _repo(tmp_path)

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-w4b", "redacted")
        await log.start()
        set_current_advanced_log(log)
        try:
            await LoggingAgentEventSink().emit(
                AgentDoneEvent(
                    session_name="crow-1",
                    outcome="done",
                    timestamp=datetime.now(timezone.utc),
                )
            )
            await log.stop()
            return log._db_path
        finally:
            set_current_advanced_log(NullAdvancedLog())

    db_path_result = asyncio.run(_run())
    assert _rows(db_path_result, "agent_records") == []
