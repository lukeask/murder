from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from murder.bus import Bus, HeartbeatEvent, Role
from murder.bus.broker import DurableBroker
from murder.bus.protocol import (
    WIRE_MESSAGE_ADAPTER,
    AckMessage,
    ClientKind,
    EventFilter,
    HelloBody,
    HelloMessage,
    PubMessage,
    RpcArgs,
    RpcMessage,
    SubArgs,
    SubMessage,
)
from murder.bus.transport_socket import SocketBusServer
from murder.db import insert_run


async def _send(writer: asyncio.StreamWriter, message: object) -> None:
    payload = json.dumps(message.model_dump(mode="json"), default=str)
    writer.write((payload + "\n").encode("utf-8"))
    await writer.drain()


async def _recv(reader: asyncio.StreamReader) -> object:
    line = await asyncio.wait_for(reader.readline(), timeout=1)
    assert line, "expected wire message"
    return WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))


@pytest.mark.asyncio
async def test_socket_server_subscription_replay_and_rpc(
    memdb: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    insert_run(memdb, "run-1", "{}")
    bus = Bus("run-1", db_conn=memdb)
    broker = DurableBroker(bus, memdb, poll_interval_s=0.01)
    broker.register_rpc_handler("echo", lambda body: {"echo": body.get("x")})
    sock_path = tmp_path / "bus.sock"
    server = SocketBusServer(
        broker,
        run_id="run-1",
        socket_path=sock_path,
        disconnect_debounce_s=0.05,
    )
    try:
        await server.start()
    except PermissionError:
        pytest.skip("unix socket bind not permitted in this sandbox")
    try:
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

        reader, writer = await asyncio.open_unix_connection(str(sock_path))
        await _send(
            writer,
            HelloMessage(
                correlation_id="hello-1",
                body=HelloBody(
                    protocol_version=1,
                    client_kind=ClientKind.TUI,
                    client_id="tui-1",
                ),
            ),
        )
        hello_ack = await _recv(reader)
        assert isinstance(hello_ack, AckMessage)
        assert hello_ack.correlation_id == "hello-1"

        wake = await _recv(reader)
        assert getattr(wake, "op", "") == "wake"

        await _send(
            writer,
            SubMessage(
                correlation_id="sub-1",
                args=SubArgs(
                    filter=EventFilter(role=Role.CROW),
                    since_id=0,
                ),
            ),
        )
        sub_ack = await _recv(reader)
        assert isinstance(sub_ack, AckMessage)
        assert sub_ack.correlation_id == "sub-1"
        replay_pub = await _recv(reader)
        assert isinstance(replay_pub, PubMessage)
        assert replay_pub.event.agent_id == "crow-1"
        replay_done = await _recv(reader)
        assert isinstance(replay_done, AckMessage)
        assert replay_done.body.kind == "replay_done"

        await _send(
            writer,
            RpcMessage(
                correlation_id="rpc-1",
                args=RpcArgs(target="echo", body={"x": 3}, timeout_s=1.0),
            ),
        )
        rpc_ack = await _recv(reader)
        assert isinstance(rpc_ack, AckMessage)
        assert rpc_ack.body.kind == "rpc_reply"
        assert rpc_ack.body.result == {"echo": 3}

        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
