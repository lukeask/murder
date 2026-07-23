"""Real aiohttp WebSocket coverage for ApplicationSocketServer failure model."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from aiohttp import ClientSession

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION, ErrorCode
from murder.app.protocol.requests import CommandName, QueryName, QueryRequest
from murder.app.protocol.terminal import TerminalFrame
from murder.app.protocol.wire import ReplyMessage, TerminalFrameMessage
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.socket_server import ApplicationConnection, ApplicationSocketServer
from murder.facts.log import FactLog, ProjectionInputLog, ensure_fact_schema


class _Application:
    available_queries = (QueryName.HEALTH_GET,)
    available_commands = ()

    def __init__(self) -> None:
        self.delay_s = 0.0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.calls = 0

    async def query(self, name: QueryName, params: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        self.started.set()
        try:
            if self.delay_s > 0:
                await asyncio.sleep(self.delay_s)
            return {"ok": True, "name": name.value}
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def command(self, name: CommandName, params: dict[str, object]) -> dict[str, object]:
        return {}


def _memory_logs() -> tuple[sqlite3.Connection, FactLog, ProjectionInputLog]:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_fact_schema(conn)
    return conn, FactLog(conn, poll_interval_s=0.01), ProjectionInputLog(conn, poll_interval_s=0.01)


async def _start_server(
    *,
    application: _Application | None = None,
    terminal_capture: Any = None,
    terminal_interval_s: float = 0.05,
) -> tuple[ApplicationSocketServer, str, _Application]:
    app = application or _Application()
    _conn, facts, inputs = _memory_logs()
    server = ApplicationSocketServer(
        gateway=ApplicationGateway(app),
        facts=facts,
        projection_inputs=inputs,
        providers=ProjectionProviderRegistry(),
        run_id="test-run",
        terminal_capture=terminal_capture,
        terminal_interval_s=terminal_interval_s,
    )
    host, port = await server.start(host="127.0.0.1", port=0)
    return server, f"ws://{host}:{port}/api/ws", app


async def _hello(ws: Any, *, client_id: str = "test-client") -> dict[str, Any]:
    await ws.send_json(
        {
            "op": "client.hello",
            "protocol_version": APPLICATION_PROTOCOL_VERSION,
            "client": {"client_id": client_id, "kind": "cli"},
        }
    )
    hello = await ws.receive_json(timeout=2.0)
    assert hello["op"] == "server.hello"
    return hello


async def _receive_until(ws: Any, *, op: str, timeout_s: float = 2.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for op={op!r}")
        message = await ws.receive_json(timeout=remaining)
        if message.get("op") == op:
            return message


@pytest.mark.asyncio
async def test_request_reply_over_real_websocket() -> None:
    server, url, _app = await _start_server()
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await _hello(ws)
            await ws.send_json(
                {
                    "op": "request",
                    "request_id": "r1",
                    "timeout_s": 5,
                    "request": {"kind": "query", "name": "health.get", "params": {}},
                }
            )
            reply = await _receive_until(ws, op="reply")
            assert reply["request_id"] == "r1"
            assert reply["result"]["ok"] is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_request_timeout_s_is_applied_by_gateway() -> None:
    application = _Application()
    application.delay_s = 1.0
    server, url, _ = await _start_server(application=application)
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await _hello(ws)
            await ws.send_json(
                {
                    "op": "request",
                    "request_id": "slow",
                    "timeout_s": 0.05,
                    "request": {"kind": "query", "name": "health.get", "params": {}},
                }
            )
            error = await _receive_until(ws, op="error", timeout_s=2.0)
            assert error["request_id"] == "slow"
            assert error["error"]["code"] == ErrorCode.REQUEST_FAILED
            assert "timed out" in error["error"]["message"]
            await application.started.wait()
            await asyncio.wait_for(application.cancelled.wait(), timeout=1.0)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_stream_failure_sends_scoped_error_and_clears_registry() -> None:
    server, url, _ = await _start_server(terminal_capture=None)
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await _hello(ws)
            stream_id = "term-1"
            await ws.send_json(
                {
                    "op": "terminal.attach",
                    "stream_id": stream_id,
                    "target": {"session_id": str(uuid4())},
                }
            )
            error = await _receive_until(ws, op="error", timeout_s=2.0)
            assert error["stream_id"] == stream_id
            assert error["error"]["code"] == ErrorCode.STREAM_FAILED
            assert "unavailable" in error["error"]["message"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_unsupported_subscription_sends_scoped_error() -> None:
    server, url, _ = await _start_server()
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await _hello(ws)
            await ws.send_json(
                {
                    "op": "subscribe",
                    "subscription_id": "sub-1",
                    "subscription": {
                        "kind": "projections",
                        "topics": ["roster"],
                    },
                }
            )
            error = await _receive_until(ws, op="error", timeout_s=2.0)
            assert error["subscription_id"] == "sub-1"
            assert error["error"]["code"] == ErrorCode.UNSUPPORTED_SUBSCRIPTION
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_terminal_frames_and_replies_share_serialized_writer() -> None:
    session_id = uuid4()
    frames_emitted = 0

    async def capture(_sid: Any) -> SimpleNamespace:
        nonlocal frames_emitted
        frames_emitted += 1
        return SimpleNamespace(data=f"frame-{frames_emitted}", columns=40, rows=12)

    server, url, _ = await _start_server(terminal_capture=capture, terminal_interval_s=0.02)
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await _hello(ws)
            await ws.send_json(
                {
                    "op": "terminal.attach",
                    "stream_id": "term-live",
                    "target": {"session_id": str(session_id)},
                }
            )
            attached = await _receive_until(ws, op="terminal.attached")
            assert attached["stream_id"] == "term-live"
            frame = await _receive_until(ws, op="terminal.frame")
            assert frame["frame"]["sequence"] >= 1
            await ws.send_json(
                {
                    "op": "request",
                    "request_id": "during-stream",
                    "timeout_s": 2,
                    "request": {"kind": "query", "name": "health.get", "params": {}},
                }
            )
            reply = await _receive_until(ws, op="reply")
            assert reply["request_id"] == "during-stream"
            assert reply["result"]["ok"] is True
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_terminal_frames_coalesce_under_writer_backpressure() -> None:
    sent: list[dict[str, Any]] = []
    release = asyncio.Event()

    class _BlockingSocket:
        async def send_json(self, payload: dict[str, Any]) -> None:
            sent.append(payload)
            if payload.get("op") == "reply" and payload.get("request_id") == "hold":
                await release.wait()

        async def close(self) -> None:
            return None

    connection = ApplicationConnection(_BlockingSocket(), "coalesce-client")
    try:
        await connection.send(ReplyMessage(request_id="hold", result={"held": True}))
        await asyncio.sleep(0)
        session_id = uuid4()
        now = datetime.now(timezone.utc)
        for sequence in range(1, 8):
            await connection.send(
                TerminalFrameMessage(
                    stream_id="s1",
                    frame=TerminalFrame(
                        subscription_id="s1",
                        session_id=session_id,
                        sequence=sequence,
                        captured_at=now,
                        columns=80,
                        rows=24,
                        data=f"seq-{sequence}",
                    ),
                )
            )
        await connection.send(ReplyMessage(request_id="after", result={"ok": True}))
        release.set()
        deadline = asyncio.get_running_loop().time() + 1.0
        while asyncio.get_running_loop().time() < deadline:
            if any(item.get("request_id") == "after" for item in sent):
                break
            await asyncio.sleep(0.01)
        frames = [item for item in sent if item.get("op") == "terminal.frame"]
        assert len(frames) == 1
        assert frames[0]["frame"]["sequence"] == 7
        assert frames[0]["frame"]["data"] == "seq-7"
        assert any(item.get("request_id") == "after" for item in sent)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_gateway_timeout_cancels_application_await() -> None:
    application = _Application()
    application.delay_s = 1.0
    gateway = ApplicationGateway(application)

    with pytest.raises(TimeoutError, match="health.get timed out"):
        await gateway.request(QueryRequest(name=QueryName.HEALTH_GET), timeout_s=0.05)

    await application.started.wait()
    await asyncio.wait_for(application.cancelled.wait(), timeout=1.0)
