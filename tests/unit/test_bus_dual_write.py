from __future__ import annotations

import sqlite3

import pytest

from murder.bus import Bus, CommandEvent, HeartbeatEvent, Role
from murder.db import insert_run


@pytest.mark.asyncio
async def test_publish_command_writes_commands_and_events(memdb: sqlite3.Connection) -> None:
    insert_run(memdb, "r1", "{}")
    bus = Bus(run_id="r1", db_conn=memdb)
    event = CommandEvent(
        run_id="r1",
        agent_id="crow-1",
        role=Role.CROW,
        target_worker="collaborator",
        kind="collaborator.chat_send",
        payload={"text": "hello"},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    await bus.publish(event)

    command = memdb.execute(
        "SELECT id, run_id, target_worker, kind, status FROM commands WHERE id = ?",
        (str(event.id),),
    ).fetchone()
    assert command is not None
    assert command["run_id"] == "r1"
    assert command["target_worker"] == "collaborator"
    assert command["kind"] == "collaborator.chat_send"
    assert command["status"] == "pending"

    ev = memdb.execute(
        "SELECT type, run_id, schema_version FROM events WHERE type = 'command'"
    ).fetchone()
    assert ev is not None
    assert ev["run_id"] == "r1"
    assert ev["schema_version"] == 1


@pytest.mark.asyncio
async def test_publish_non_command_writes_event_only(memdb: sqlite3.Connection) -> None:
    insert_run(memdb, "r1", "{}")
    bus = Bus(run_id="r1", db_conn=memdb)
    event = HeartbeatEvent(
        run_id="r1",
        agent_id="collaborator-1",
        role=Role.COLLABORATOR,
        state="progressing",
    )

    await bus.publish(event)

    assert memdb.execute("SELECT COUNT(*) AS n FROM commands").fetchone()["n"] == 0
    assert memdb.execute("SELECT COUNT(*) AS n FROM events WHERE type = 'heartbeat'").fetchone()["n"] == 1
