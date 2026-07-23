"""Agent-registry lifecycle capture rides the ONE bus aspect (plan §2.5.A).

The registry no longer calls ``record_agent`` directly. Its mutations fire an
``on_lifecycle`` hook → ``AgentLifecycleEvent`` on the bus → the recorder
SUBSCRIBER routes it into ``agent_records``. This test drives that REAL path
(registry hook → real ``OrchestrationNotifier.publish`` → recorder subscriber), not a hand-built
record, so it would catch a registry that forgot to fire the hook.

It also pins the carve-outs: ``reap`` emits NO event (nothing reacts to the DEAD
transition; reap is already on the bus via ``agent.stop()``), and the
``LoggingAgentEventSink`` no longer double-writes to the flight recorder (its
full payload rides the Phase 1 structured log instead).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from murder.app.service.agent_registry import AgentRegistry
from murder.runtime.agents.types import AgentStatus
from murder.runtime.orchestration.events import AgentLifecycleEvent
from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.observability.advanced_log import (
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.observability.log_context import set_run_id
from murder.runtime.agents.base import AgentRole
from murder.runtime.agents.events import AgentDoneEvent, LoggingAgentEventSink


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
        self.status = AgentStatus.RUNNING

    async def stop(self) -> None:
        return None


def test_registry_lifecycle_rides_bus_into_agent_records(tmp_path):
    repo = _repo(tmp_path)

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-w4", "redacted")
        await log.start()
        set_current_advanced_log(log)
        set_run_id("run-w4")
        bus = InProcessOrchestrationEventSink()

        async def _recorder(event):
            log.record_orchestration_event(event)

        bus.subscribe(_recorder)

        # Mirror Runtime._emit_agent_lifecycle: the sync registry hook schedules
        # an AgentLifecycleEvent publish on the running loop.
        pending: list[asyncio.Task] = []

        def _on_lifecycle(*, op, agent_id, details=None, reason=None):
            pending.append(
                asyncio.create_task(
                    bus.publish(
                        AgentLifecycleEvent(
                            run_id="run-w4",
                            agent_id=agent_id,
                            op=op,
                            details=details or {},
                            reason=reason,
                        )
                    )
                )
            )

        try:
            reg = AgentRegistry()
            reg.on_lifecycle = _on_lifecycle
            reg.register(_FakeAgent("a-1"))
            reg.rename_agent("a-1", "a-2")
            await reg.reap("a-2", tasks={}, db=None)  # NO event by design
            reg.clear()
            await asyncio.gather(*pending)
            await log.stop()
            return log._db_path
        finally:
            set_current_advanced_log(NullAdvancedLog())

    db_path = asyncio.run(_run())

    rows = _rows(db_path, "agent_records")
    payloads = [json.loads(r["payload"]) for r in rows]
    ops = [p["op"] for p in payloads]
    # register / rename / clear ride the bus; reap is deliberately absent.
    assert ops == ["register", "rename", "clear"], ops
    # The rich event carries agent_id in its envelope, and the run_id column is
    # stamped from the ambient correlation context.
    assert payloads[0]["agent_id"] == "a-1"
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

    db_path = asyncio.run(_run())
    assert _rows(db_path, "agent_records") == []
