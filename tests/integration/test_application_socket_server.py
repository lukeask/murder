"""Real aiohttp WebSocket coverage for ApplicationSocketServer failure model."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from aiohttp import ClientSession

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION, ErrorCode
from murder.app.protocol.requests import CommandName, QueryName, QueryRequest
from murder.app.service.gateway import ApplicationGateway
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.socket_server import ApplicationSocketServer
from murder.facts.log import FactLog, ProjectionInputLog
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db


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
            return {"ok": True, "pid": 1}
        except asyncio.CancelledError:
            self.cancelled.set()
            raise

    async def command(self, name: CommandName, params: dict[str, object]) -> dict[str, object]:
        return {}


def _test_logs(tmp_path: Path) -> tuple[RepoDb, FactLog, ProjectionInputLog]:
    db = open_test_repo_db(tmp_path / f"application-socket-{uuid4()}.db")
    return db, FactLog(db, poll_interval_s=0.01), ProjectionInputLog(db, poll_interval_s=0.01)


async def _start_server(
    *,
    tmp_path: Path,
    application: _Application | None = None,
    terminal_capture: Any = None,
    terminal_interval_s: float = 0.05,
) -> tuple[ApplicationSocketServer, str, _Application, RepoDb]:
    app = application or _Application()
    db, facts, inputs = _test_logs(tmp_path)
    server = ApplicationSocketServer(
        gateway=ApplicationGateway(app),
        facts=facts,
        projection_inputs=inputs,
        providers=ProjectionProviderRegistry(),
        run_id="test-run",
        terminal_capture=terminal_capture,
        terminal_interval_s=terminal_interval_s,
    )
    try:
        host, port = await server.start(host="127.0.0.1", port=0)
    except Exception:
        db.close()
        raise
    return server, f"ws://{host}:{port}/api/ws", app, db


async def _stop_server(server: ApplicationSocketServer, db: RepoDb) -> None:
    try:
        await server.stop()
    finally:
        db.close()


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
async def test_protocol_version_mismatch_sends_version_mismatch_error(tmp_path: Path) -> None:
    """Stale clients must receive version_mismatch (not a bare close) so they stop reconnecting."""
    server, url, _app, db = await _start_server(tmp_path=tmp_path)
    try:
        async with ClientSession() as http, http.ws_connect(url) as ws:
            await ws.send_json(
                {
                    "op": "client.hello",
                    "protocol_version": APPLICATION_PROTOCOL_VERSION - 1,
                    "client": {"client_id": "stale-client", "kind": "tui"},
                }
            )
            error = await _receive_until(ws, op="error", timeout_s=2.0)
            assert error["error"]["code"] == ErrorCode.VERSION_MISMATCH
            assert "protocol version mismatch" in error["error"]["message"]
    finally:
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_request_reply_over_real_websocket(tmp_path: Path) -> None:
    server, url, _app, db = await _start_server(tmp_path=tmp_path)
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
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_request_timeout_s_is_applied_by_gateway(tmp_path: Path) -> None:
    application = _Application()
    application.delay_s = 1.0
    server, url, _, db = await _start_server(tmp_path=tmp_path, application=application)
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
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_stream_failure_sends_scoped_error_and_clears_registry(tmp_path: Path) -> None:
    server, url, _, db = await _start_server(tmp_path=tmp_path, terminal_capture=None)
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
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_unsupported_subscription_sends_scoped_error(tmp_path: Path) -> None:
    server, url, _, db = await _start_server(tmp_path=tmp_path)
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
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_terminal_frames_and_replies_share_serialized_writer(tmp_path: Path) -> None:
    session_id = uuid4()
    frames_emitted = 0

    async def capture(_sid: Any) -> SimpleNamespace:
        nonlocal frames_emitted
        frames_emitted += 1
        return SimpleNamespace(data=f"frame-{frames_emitted}", columns=40, rows=12)

    server, url, _, db = await _start_server(
        tmp_path=tmp_path,
        terminal_capture=capture,
        terminal_interval_s=0.02,
    )
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
        await _stop_server(server, db)


@pytest.mark.asyncio
async def test_gateway_timeout_cancels_application_await() -> None:
    application = _Application()
    application.delay_s = 1.0
    gateway = ApplicationGateway(application)

    with pytest.raises(TimeoutError, match="request timed out after"):
        await gateway.request(QueryRequest(name=QueryName.HEALTH_GET), timeout_s=0.05)

    await application.started.wait()
    await asyncio.wait_for(application.cancelled.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_static_assets_served_from_assets_dir(tmp_path: Path) -> None:
    assets = tmp_path / "spa"
    assets.mkdir()
    assets.joinpath("index.html").write_text("<html>spa</html>", encoding="utf-8")
    asset_dir = assets / "assets"
    asset_dir.mkdir()
    asset_dir.joinpath("app.js").write_text("console.log('ok')", encoding="utf-8")

    db, facts, inputs = _test_logs(tmp_path)
    server = ApplicationSocketServer(
        gateway=ApplicationGateway(_Application()),
        facts=facts,
        projection_inputs=inputs,
        providers=ProjectionProviderRegistry(),
        run_id="test-run",
        assets_dir=assets,
    )
    try:
        host, port = await server.start(host="127.0.0.1", port=0)
        base = f"http://{host}:{port}"
        async with ClientSession() as http:
            async with http.get(f"{base}/") as resp:
                assert resp.status == 200
                assert "spa" in await resp.text()
            async with http.get(f"{base}/index.html") as resp:
                assert resp.status == 200
            async with http.get(f"{base}/assets/app.js") as resp:
                assert resp.status == 200
                assert "ok" in await resp.text()
            async with http.get(f"{base}/missing-route") as resp:
                assert resp.status == 200
                assert "spa" in await resp.text()
    finally:
        await _stop_server(server, db)
