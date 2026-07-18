"""Tests for the murder web bridge: relay framing, scope->host, SPA fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import typer

from murder.app.cli import web_cmd
from murder.web import bridge

# ---------------------------------------------------------------------------
# Relay framing: real loopback unix socket + aiohttp test WS endpoint
# ---------------------------------------------------------------------------


def test_relay_frames_lines_and_appends_newline(tmp_path: Path) -> None:
    """A fake unix-socket peer emits multi-line and partial-line JSON; the WS
    side must receive each complete line as one message, and WS->socket must
    append a newline."""
    from aiohttp import web as aioweb
    from aiohttp.test_utils import TestClient, TestServer

    socket_path = tmp_path / "bus.sock"
    received_from_ws: list[bytes] = []

    async def _scenario() -> None:
        # Fake service socket: when the bridge connects, dribble framed JSON in pieces,
        # then record whatever the bridge writes back.
        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # Two complete lines arriving in one chunk, plus a split line.
            writer.write(b'{"a":1}\n{"b":2}\n{"c":')
            await writer.drain()
            await asyncio.sleep(0.02)
            writer.write(b"3}\n")  # completes the third line
            await writer.drain()
            # Read one line the WS sent inbound.
            line = await reader.readline()
            received_from_ws.append(line)
            await asyncio.sleep(0.05)
            writer.close()

        server = await asyncio.start_unix_server(_handle, path=str(socket_path))

        app = aioweb.Application()
        app.router.add_get("/api/ws", bridge._make_application_handler(socket_path))
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()
        try:
            ws = await client.ws_connect("/api/ws")
            # Send one message WS->socket; bridge must append "\n".
            await ws.send_str('{"hello":"world"}')

            got: list[str] = []
            while len(got) < 3:
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                if msg.type.name == "TEXT":
                    got.append(msg.data)
                else:
                    break
            assert got == ['{"a":1}', '{"b":2}', '{"c":3}']
            await ws.close()
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(_scenario())
    assert received_from_ws == [b'{"hello":"world"}\n']


def test_relay_closes_ws_when_bus_unreachable(tmp_path: Path) -> None:
    """No unix socket present -> the bridge closes the WS instead of hanging."""
    from aiohttp import web as aioweb
    from aiohttp.test_utils import TestClient, TestServer

    socket_path = tmp_path / "absent.sock"

    async def _scenario() -> None:
        app = aioweb.Application()
        app.router.add_get("/api/ws", bridge._make_application_handler(socket_path))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/api/ws")
            msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
            assert msg.type.name in ("CLOSE", "CLOSED", "CLOSING")
        finally:
            await client.close()

    asyncio.run(_scenario())


def test_websocket_speaks_service_application_protocol(tmp_path: Path) -> None:
    """The framing edge reaches the service gateway, never a browser-owned RPC mapper."""
    from aiohttp import web as aioweb
    from aiohttp.test_utils import TestClient, TestServer

    from murder.bus.transport_socket import SocketBusServer

    class _Broker:
        def __init__(self) -> None:
            self.published: list[object] = []

        async def request(self, target: str, body: dict, *, timeout_s: float) -> dict:
            return {"target": target, "body": body, "timeout_s": timeout_s}

        async def publish(self, event: object) -> None:
            self.published.append(event)

        def watermark(self) -> int:
            return 0

        def fact_watermark(self) -> int:
            return 0

        def projection_watermark(self) -> int:
            return 0

        def replay(self, *_args: object, **_kwargs: object) -> list:
            return []

        async def tail(self, *_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
            while True:
                await asyncio.sleep(3600)
            yield

    socket_path = tmp_path / "service.sock"

    async def _scenario() -> None:
        service = SocketBusServer(
            _Broker(),  # type: ignore[arg-type]
            run_id="run-web-test",
            socket_path=socket_path,
        )
        try:
            await service.start()
        except PermissionError:
            pytest.skip("sandbox forbids Unix-domain socket creation")
        app = aioweb.Application()
        app.router.add_get("/api/ws", bridge._make_application_handler(socket_path))
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/api/ws")
            await ws.send_json(
                {
                    "op": "client.hello",
                    "protocol_version": 1,
                    "client": {"client_id": "web-test", "kind": "web"},
                }
            )
            hello = await ws.receive_json(timeout=2)
            assert hello["op"] == "server.hello"
            await ws.send_json(
                {
                    "op": "request",
                    "request_id": "health-1",
                    "request": {"kind": "query", "name": "health.get", "params": {}},
                    "timeout_s": 2,
                }
            )
            reply = await ws.receive_json(timeout=2)
            assert reply["op"] == "reply"
            assert reply["request_id"] == "health-1"
            assert reply["result"]["target"] == "health.ping"
            await ws.close()
        finally:
            await client.close()
            await service.stop()

    asyncio.run(_scenario())


# ---------------------------------------------------------------------------
# Scope -> bind host
# ---------------------------------------------------------------------------


def test_resolve_host_localhost_default() -> None:
    host, hint = web_cmd.resolve_host(localhost=True, lan=False, tailnet=False)
    assert host == "127.0.0.1"
    assert hint is None


def test_resolve_host_lan() -> None:
    host, hint = web_cmd.resolve_host(localhost=False, lan=True, tailnet=False)
    assert host == "0.0.0.0"
    assert hint is None


def test_resolve_host_tailnet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_cmd, "_tailscale_ipv4", lambda: "100.101.102.103")
    host, hint = web_cmd.resolve_host(localhost=False, lan=False, tailnet=True)
    assert host == "100.101.102.103"
    assert hint is not None and "tailscale serve" in hint


def test_resolve_host_tailnet_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_cmd, "_tailscale_ipv4", lambda: None)
    with pytest.raises(typer.BadParameter):
        web_cmd.resolve_host(localhost=False, lan=False, tailnet=True)


def test_resolve_host_mutually_exclusive() -> None:
    with pytest.raises(typer.BadParameter):
        web_cmd.resolve_host(localhost=False, lan=True, tailnet=True)


def test_tailscale_ipv4_picks_first_100(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as sp

    monkeypatch.setattr(web_cmd.shutil, "which", lambda _: "/usr/bin/tailscale")

    class _Result:
        returncode = 0
        stdout = "fd7a:115c::1\n100.64.0.5\n"

    monkeypatch.setattr(web_cmd.subprocess, "run", lambda *a, **k: _Result())
    assert web_cmd._tailscale_ipv4() == "100.64.0.5"
    del sp


# ---------------------------------------------------------------------------
# Static SPA fallback
# ---------------------------------------------------------------------------


def test_resolve_assets_dir_packaged_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the packaged bundle exists it takes precedence over the source build."""
    packaged = tmp_path / "_webui"
    packaged.mkdir()
    monkeypatch.setattr(bridge, "_packaged_assets_dir", lambda: packaged)
    repo = tmp_path / "repo"
    (repo / "webui" / "dist").mkdir(parents=True)
    assert bridge.resolve_assets_dir(repo) == packaged


def test_resolve_assets_dir_source_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the packaged lookup at a guaranteed-nonexistent dir so this branch
    # is exercised deterministically regardless of any local _webui artifact.
    monkeypatch.setattr(bridge, "_packaged_assets_dir", lambda: tmp_path / "no_packaged")
    repo = tmp_path / "repo"
    (repo / "webui" / "dist").mkdir(parents=True)
    (repo / "webui" / "dist" / "index.html").write_text("<html></html>")
    assert bridge.resolve_assets_dir(repo) == repo / "webui" / "dist"


def test_resolve_assets_dir_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_packaged_assets_dir", lambda: tmp_path / "no_packaged")
    with pytest.raises(FileNotFoundError):
        bridge.resolve_assets_dir(tmp_path / "nope")


def test_static_spa_fallback(tmp_path: Path) -> None:
    """Unknown non-asset path returns index.html; real asset is served as-is."""
    from aiohttp.test_utils import TestClient, TestServer

    assets = tmp_path / "dist"
    assets.mkdir()
    (assets / "index.html").write_text("<!doctype html>INDEX")
    (assets / "app.js").write_text("console.log(1)")

    async def _scenario() -> None:
        app = bridge.create_app(tmp_path / "bus.sock", assets)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            # Real asset.
            r = await client.get("/app.js")
            assert r.status == 200
            assert "console.log" in await r.text()
            # Unknown route -> SPA index.html.
            r = await client.get("/tickets/abc123")
            assert r.status == 200
            assert "INDEX" in await r.text()
            # Root.
            r = await client.get("/")
            assert r.status == 200
            assert "INDEX" in await r.text()
        finally:
            await client.close()

    asyncio.run(_scenario())


def test_health_endpoint(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    assets = tmp_path / "dist"
    assets.mkdir()
    (assets / "index.html").write_text("INDEX")

    async def _scenario() -> None:
        app = bridge.create_app(tmp_path / "absent.sock", assets)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/api/health")
            assert r.status == 200
            body = await r.json()
            assert body["ok"] is True
            assert body["service_reachable"] is False
        finally:
            await client.close()

    asyncio.run(_scenario())
