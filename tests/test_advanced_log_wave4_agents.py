"""Wave-4 boundary instrumentation: agent sink (#6a) + state_mutations (#2.4).

Drives the ``LoggingAgentEventSink.emit`` agent-record seam and the
agent-registry lifecycle seam against a REAL in-temp ``AdvancedLog`` (redacted
mode) pinned via ``set_current_advanced_log``, then asserts rows land in
``agent_records``. Resets the accessor to Null in ``finally``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from murder.observability.advanced_log import (
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.runtime.agents.events import AgentDoneEvent, LoggingAgentEventSink


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def _count(db_path: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


def test_agent_sink_and_registry_record_rows(tmp_path):
    repo = _repo(tmp_path)

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-w4", "redacted")
        await log.start()
        set_current_advanced_log(log)
        try:
            sink = LoggingAgentEventSink()
            await sink.emit(
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

    db_path = asyncio.run(_run())

    rows = _count(db_path, "agent_records")
    assert len(rows) == 1, rows
    import json

    payload = json.loads(rows[0]["payload"])
    assert payload["event_type"] == "AgentDoneEvent"
    assert payload["session_name"] == "crow-1"
    assert payload["outcome"] == "done"


def test_registry_lifecycle_records_agent_ops(tmp_path):
    repo = _repo(tmp_path)

    from murder.app.service.agent_registry import AgentRegistry
    from murder.runtime.agents.base import AgentRole
    from murder.bus import AgentStatus

    class _FakeAgent:
        def __init__(self, agent_id: str) -> None:
            self.id = agent_id
            self.role = AgentRole.CROW
            self.ticket_id = "T-1"
            self.status = AgentStatus.RUNNING

        async def stop(self) -> None:
            return None

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-w4b", "redacted")
        await log.start()
        set_current_advanced_log(log)
        try:
            reg = AgentRegistry()
            agent = _FakeAgent("a-1")
            reg.register(agent)
            reg.rename_agent("a-1", "a-2")
            await reg.reap("a-2", tasks={}, db=None)
            reg.clear()
            await log.stop()
            return log._db_path
        finally:
            set_current_advanced_log(NullAdvancedLog())

    db_path = asyncio.run(_run())

    import json

    rows = _count(db_path, "agent_records")
    ops = [json.loads(r["payload"])["op"] for r in rows]
    assert ops == ["register", "rename", "reap", "clear"], ops
