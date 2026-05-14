from __future__ import annotations

import asyncio
import sqlite3

import pytest

from murder.bus import Bus, EventFilter, HeartbeatEvent, Role
from murder.bus.broker import (
    DurableBroker,
    InProcessBroker,
    UnsupportedReplayError,
    UnsupportedRpcError,
)
from murder.db import insert_run


async def test_inprocess_broker_subscribe_applies_filter() -> None:
    bus = Bus("run-1")
    broker = InProcessBroker(bus)
    stream = broker.subscribe(EventFilter(role=Role.CROW))
    next_event = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)

    await broker.publish(
        HeartbeatEvent(
            run_id="run-1",
            agent_id="collab-1",
            role=Role.COLLABORATOR,
            state="thinking",
        )
    )
    await broker.publish(
        HeartbeatEvent(
            run_id="run-1",
            agent_id="crow-1",
            role=Role.CROW,
            state="progressing",
        )
    )

    event = await asyncio.wait_for(next_event, timeout=1)
    assert event.agent_id == "crow-1"

    await stream.aclose()


async def test_inprocess_broker_rejects_replay_until_durable_broker_exists() -> None:
    broker = InProcessBroker(Bus("run-1"))
    stream = broker.subscribe(since_id=10)

    with pytest.raises(UnsupportedReplayError):
        await stream.__anext__()


async def test_inprocess_broker_rejects_rpc_until_router_exists() -> None:
    broker = InProcessBroker(Bus("run-1"))

    with pytest.raises(UnsupportedRpcError):
        await broker.request("worker", {}, timeout_s=1.0)


@pytest.mark.asyncio
async def test_durable_broker_replays_then_tails(memdb: sqlite3.Connection) -> None:
    insert_run(memdb, "run-1", "{}")
    bus = Bus("run-1", db_conn=memdb)
    broker = DurableBroker(bus, memdb, poll_interval_s=0.01)

    await broker.publish(
        HeartbeatEvent(
            run_id="run-1",
            agent_id="crow-1",
            role=Role.CROW,
            state="progressing",
        )
    )
    watermark = broker.watermark()
    stream = broker.subscribe(EventFilter(role=Role.CROW), since_id=0)
    replayed = await asyncio.wait_for(stream.__anext__(), timeout=1)
    assert replayed.agent_id == "crow-1"
    assert broker.watermark() >= watermark

    await broker.publish(
        HeartbeatEvent(
            run_id="run-1",
            agent_id="crow-2",
            role=Role.CROW,
            state="thinking",
        )
    )
    tailed = await asyncio.wait_for(stream.__anext__(), timeout=1)
    assert tailed.agent_id == "crow-2"
    await stream.aclose()


@pytest.mark.asyncio
async def test_durable_broker_rpc_handler(memdb: sqlite3.Connection) -> None:
    insert_run(memdb, "run-1", "{}")
    broker = DurableBroker(Bus("run-1", db_conn=memdb), memdb)

    async def _echo(body: dict) -> dict:
        return {"echo": body.get("value")}

    broker.register_rpc_handler("echo", _echo)
    result = await broker.request("echo", {"value": 7}, timeout_s=1.0)
    assert result == {"echo": 7}
