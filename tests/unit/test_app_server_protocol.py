"""Unit tests for Codex app-server JSON-RPC protocol + connection."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from murder.llm.harness_control.app_server import (
    AppServerClient,
    AppServerConnection,
    AppServerRpcError,
    RpcError,
    RpcNotification,
    RpcRequest,
    RpcResponse,
    decode_line,
    encode_message,
    is_notification,
    is_request,
    is_response,
    message_kind,
)


class FakeTransport:
    """Scripted stdout lines + captured stdin writes for connection tests."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self.written: list[str] = []
        self._outbound: asyncio.Queue[str | None] = asyncio.Queue()
        for line in lines or []:
            self._outbound.put_nowait(line)
        self.closed = False

    def push(self, line: str) -> None:
        self._outbound.put_nowait(line)

    def push_eof(self) -> None:
        self._outbound.put_nowait(None)

    async def write_line(self, line: str) -> None:
        self.written.append(line)

    async def readline(self) -> str:
        item = await self._outbound.get()
        if item is None:
            return ""
        return item

    async def aclose(self) -> None:
        self.closed = True
        self.push_eof()


class ShutdownOrderingTransport:
    """Transport that verifies stdout remains active while shutdown starts."""

    def __init__(self) -> None:
        self.reader_cancelled = asyncio.Event()
        self.closed = False

    async def write_line(self, line: str) -> None:
        del line

    async def readline(self) -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.reader_cancelled.set()
            raise
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        assert not self.reader_cancelled.is_set()
        self.closed = True


def test_encode_decode_request_round_trip() -> None:
    message = RpcRequest(id=1, method="initialize", params={"clientInfo": {"name": "murder"}})
    encoded = encode_message(message)
    assert "jsonrpc" not in encoded
    assert json.loads(encoded) == {
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "murder"}},
    }
    decoded = decode_line(encoded)
    assert is_request(decoded)
    assert decoded == message
    assert message_kind(decoded) == "request"


def test_encode_decode_notification_and_responses() -> None:
    notification = RpcNotification(method="initialized")
    assert json.loads(encode_message(notification)) == {"method": "initialized"}
    assert decode_line(encode_message(notification)) == notification
    assert is_notification(notification)

    ok = RpcResponse(id="abc", result={"userAgent": "codex"})
    assert json.loads(encode_message(ok)) == {"id": "abc", "result": {"userAgent": "codex"}}
    assert decode_line(encode_message(ok)) == ok
    assert is_response(ok)

    err = RpcResponse(id=2, error=RpcError(code=-32001, message="overloaded", data={"retry": True}))
    payload = json.loads(encode_message(err))
    assert payload == {
        "id": 2,
        "error": {"code": -32001, "message": "overloaded", "data": {"retry": True}},
    }
    assert decode_line(encode_message(err)) == err


def test_decode_strips_jsonrpc_field_if_present() -> None:
    decoded = decode_line('{"jsonrpc":"2.0","method":"turn/started","params":{"turn":{"id":"t1"}}}')
    assert decoded == RpcNotification(method="turn/started", params={"turn": {"id": "t1"}})


def test_decode_rejects_ambiguous_shapes() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        decode_line('{"id":1}')
    with pytest.raises(ValueError, match="empty"):
        decode_line("   ")


def test_connection_resolves_pending_request_and_queues_events() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()

        request_task = asyncio.create_task(connection.request("thread/start", {"cwd": "/tmp"}))
        await asyncio.sleep(0)
        assert len(transport.written) == 1
        outbound = json.loads(transport.written[0])
        assert outbound["method"] == "thread/start"
        assert outbound["params"] == {"cwd": "/tmp"}
        request_id = outbound["id"]

        transport.push(
            json.dumps({"method": "thread/started", "params": {"thread": {"id": "th1"}}})
        )
        transport.push(
            json.dumps(
                {
                    "id": 99,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"command": "ls"},
                }
            )
        )
        transport.push(json.dumps({"id": request_id, "result": {"thread": {"id": "th1"}}}))

        result = await asyncio.wait_for(request_task, timeout=1.0)
        assert result == {"thread": {"id": "th1"}}

        notifications = []
        for _ in range(20):
            notifications = connection.drain_notifications()
            if notifications:
                break
            await asyncio.sleep(0.01)
        assert notifications == [
            RpcNotification(method="thread/started", params={"thread": {"id": "th1"}})
        ]

        incoming = await asyncio.wait_for(connection.incoming_requests.get(), timeout=1.0)
        assert incoming == RpcRequest(
            id=99,
            method="item/commandExecution/requestApproval",
            params={"command": "ls"},
        )

        await connection.respond(99, result={"decision": "accept"})
        assert json.loads(transport.written[-1]) == {"id": 99, "result": {"decision": "accept"}}

        await connection.notify("initialized")
        assert json.loads(transport.written[-1]) == {"method": "initialized"}

        assert connection.thread_id is None
        assert connection.staged_composer_text == ""
        connection.staged_composer_text = "hello"
        connection.thread_id = "th1"
        assert connection.staged_composer_text == "hello"

        await connection.aclose()
        assert transport.closed

    asyncio.run(scenario())


def test_connection_raises_rpc_error_on_error_response() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()
        task = asyncio.create_task(
            connection.request("turn/interrupt", {"threadId": "a", "turnId": "b"})
        )
        await asyncio.sleep(0)
        request_id = json.loads(transport.written[0])["id"]
        error_code = -32602
        transport.push(
            json.dumps(
                {
                    "id": request_id,
                    "error": {"code": error_code, "message": "missing turn"},
                }
            )
        )
        with pytest.raises(AppServerRpcError, match="missing turn") as raised:
            await asyncio.wait_for(task, timeout=1.0)
        assert raised.value.error.code == error_code
        await connection.aclose()

    asyncio.run(scenario())


def test_process_connection_continuously_drains_stderr() -> None:
    async def scenario() -> None:
        script = """
import json
import sys

sys.stderr.write("x" * (2 * 1024 * 1024))
sys.stderr.flush()
request = json.loads(sys.stdin.readline())
print(json.dumps({"id": request["id"], "result": {"ok": True}}), flush=True)
"""
        connection = AppServerConnection(argv=(sys.executable, "-c", script))
        await connection.start()
        assert await asyncio.wait_for(connection.request("initialize"), timeout=2.0) == {"ok": True}

        stderr_task = connection._stderr_task
        await connection.aclose()
        assert stderr_task is not None and stderr_task.done()

    asyncio.run(scenario())


def test_connection_keeps_stdout_reader_running_during_shutdown() -> None:
    async def scenario() -> None:
        transport = ShutdownOrderingTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()
        await asyncio.sleep(0)

        await connection.aclose()

        assert transport.closed
        assert transport.reader_cancelled.is_set()

    asyncio.run(scenario())


def test_connection_eof_fails_pending_rpc_and_flips_liveness() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()

        task = asyncio.create_task(connection.request("thread/start", {"cwd": "/tmp"}))
        await asyncio.sleep(0)
        assert connection.started

        transport.push_eof()
        with pytest.raises(ConnectionError, match="stdout closed"):
            await asyncio.wait_for(task, timeout=1.0)
        assert connection.started is False

        with pytest.raises(ConnectionError, match="transport exited"):
            await connection.request("turn/interrupt", {"threadId": "a", "turnId": "b"})
        with pytest.raises(ConnectionError, match="transport exited"):
            await connection.notify("initialized")

        await connection.aclose()

    asyncio.run(scenario())


def test_connection_eof_terminates_iter_notifications() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()

        async def consume() -> list[RpcNotification]:
            return [n async for n in connection.iter_notifications()]

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        transport.push_eof()
        assert await asyncio.wait_for(task, timeout=1.0) == []
        assert connection.started is False
        await connection.aclose()

    asyncio.run(scenario())


def test_connection_eof_after_notification_drains_then_terminates() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        await connection.start()

        async def consume() -> list[RpcNotification]:
            return [n async for n in connection.iter_notifications()]

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        transport.push(
            json.dumps({"method": "thread/started", "params": {"thread": {"id": "th1"}}})
        )
        transport.push_eof()
        notifications = await asyncio.wait_for(task, timeout=1.0)
        assert notifications == [
            RpcNotification(method="thread/started", params={"thread": {"id": "th1"}})
        ]
        assert connection.started is False
        await connection.aclose()

    asyncio.run(scenario())


def test_client_initialize_handshake_and_thread_helpers() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AppServerConnection(transport=transport)
        client = AppServerClient(connection)
        await connection.start()

        init_task = asyncio.create_task(
            client.initialize(client_name="murder", client_version="0.0.1")
        )
        await asyncio.sleep(0)
        init_req = json.loads(transport.written[0])
        assert init_req["method"] == "initialize"
        assert init_req["params"]["clientInfo"] == {"name": "murder", "version": "0.0.1"}
        transport.push(
            json.dumps(
                {
                    "id": init_req["id"],
                    "result": {
                        "userAgent": "codex_app_server",
                        "codexHome": "/home/x/.codex",
                        "platformOs": "linux",
                        "platformFamily": "unix",
                    },
                }
            )
        )
        init_result = await asyncio.wait_for(init_task, timeout=1.0)
        assert init_result["platformOs"] == "linux"
        assert json.loads(transport.written[1]) == {"method": "initialized"}

        start_task = asyncio.create_task(
            client.thread_start(cwd="/work", model="gpt-5.6-sol", model_provider="local")
        )
        await asyncio.sleep(0)
        start_req = json.loads(transport.written[-1])
        assert start_req["method"] == "thread/start"
        assert start_req["params"] == {
            "cwd": "/work",
            "model": "gpt-5.6-sol",
            "modelProvider": "local",
        }
        transport.push(
            json.dumps(
                {
                    "id": start_req["id"],
                    "result": {"thread": {"id": "thread-1"}, "model": "gpt-5.6-sol"},
                }
            )
        )
        start_result = await asyncio.wait_for(start_task, timeout=1.0)
        assert start_result["thread"]["id"] == "thread-1"
        assert connection.thread_id == "thread-1"

        connection.staged_composer_text = "staged prompt"
        turn_task = asyncio.create_task(
            client.turn_start("thread-1", "hello", model="gpt-5", effort="high")
        )
        await asyncio.sleep(0)
        turn_req = json.loads(transport.written[-1])
        assert turn_req["method"] == "turn/start"
        assert turn_req["params"] == {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "hello"}],
            "model": "gpt-5",
            "effort": "high",
        }
        # Client helpers do not clear staged composer; W4 adapter owns that.
        assert connection.staged_composer_text == "staged prompt"
        transport.push(json.dumps({"id": turn_req["id"], "result": {"turn": {"id": "turn-9"}}}))
        turn_result = await asyncio.wait_for(turn_task, timeout=1.0)
        assert turn_result["turn"]["id"] == "turn-9"

        interrupt_task = asyncio.create_task(client.turn_interrupt("thread-1", "turn-9"))
        await asyncio.sleep(0)
        interrupt_req = json.loads(transport.written[-1])
        assert interrupt_req["method"] == "turn/interrupt"
        assert interrupt_req["params"] == {"threadId": "thread-1", "turnId": "turn-9"}
        transport.push(json.dumps({"id": interrupt_req["id"], "result": {}}))
        await asyncio.wait_for(interrupt_task, timeout=1.0)

        await client.respond_approval(42, decision="decline")
        assert json.loads(transport.written[-1]) == {
            "id": 42,
            "result": {"decision": "decline"},
        }

        await connection.aclose()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_start_app_server_session_closes_connection_when_initialize_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-start handshake failure must aclose the live app-server connection."""

    from murder.llm.harness_control.app_server.bootstrap import start_app_server_session

    closed: list[bool] = []

    class _StartedConnection:
        desired_model: str | None = None
        desired_model_provider: str | None = None
        desired_effort: str | None = None

        async def start(self) -> None:
            return None

        async def aclose(self) -> None:
            closed.append(True)

    class _FailingClient:
        def __init__(self, connection: _StartedConnection) -> None:
            self.connection = connection

        async def initialize(self, **_kwargs: object) -> None:
            raise RuntimeError("app-server initialize failed")

    monkeypatch.setattr(
        "murder.llm.harness_control.app_server.bootstrap.AppServerConnection",
        lambda **_kwargs: _StartedConnection(),
    )
    monkeypatch.setattr(
        "murder.llm.harness_control.app_server.bootstrap.AppServerClient",
        _FailingClient,
    )

    with pytest.raises(RuntimeError, match="app-server initialize failed"):
        await start_app_server_session(cwd="/tmp")
    assert closed == [True]


def test_read_codex_model_provider_from_top_level_config(tmp_path) -> None:
    from murder.llm.harness_control.app_server.bootstrap import (
        read_codex_model_provider,
        resolve_app_server_model_provider,
    )

    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-5.6-sol"\n'
        'model_provider = "local"\n'
        "\n"
        "[model_providers.local]\n"
        'name = "Local Inference"\n'
        'base_url = "http://localhost:54321/v1"\n',
        encoding="utf-8",
    )
    assert read_codex_model_provider(config_path=config) == "local"
    assert resolve_app_server_model_provider(config_path=config) == "local"
    assert resolve_app_server_model_provider("openai", config_path=config) == "openai"
    # Blank Murder override falls through to Codex config.
    assert resolve_app_server_model_provider("  ", config_path=config) == "local"


def test_read_codex_model_provider_missing_or_section_only(tmp_path) -> None:
    from murder.llm.harness_control.app_server.bootstrap import read_codex_model_provider

    missing = tmp_path / "absent.toml"
    assert read_codex_model_provider(config_path=missing) is None

    section_only = tmp_path / "section.toml"
    section_only.write_text(
        'model = "gpt-5"\n\n[model_providers.local]\nname = "Local"\n',
        encoding="utf-8",
    )
    assert read_codex_model_provider(config_path=section_only) is None


@pytest.mark.asyncio
async def test_start_app_server_session_passes_model_provider_to_thread_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Bootstrap must forward modelProvider on thread/start when known."""

    from murder.llm.harness_control.app_server.bootstrap import start_app_server_session

    thread_starts: list[dict[str, object]] = []

    class _StartedConnection:
        desired_model: str | None = None
        desired_model_provider: str | None = None
        desired_effort: str | None = None

        async def start(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    class _RecordingClient:
        def __init__(self, connection: _StartedConnection) -> None:
            self.connection = connection

        async def initialize(self, **_kwargs: object) -> dict[str, object]:
            return {}

        async def thread_start(self, **kwargs: object) -> dict[str, object]:
            thread_starts.append(dict(kwargs))
            return {"thread": {"id": "th-local"}}

    monkeypatch.setattr(
        "murder.llm.harness_control.app_server.bootstrap.AppServerConnection",
        lambda **_kwargs: _StartedConnection(),
    )
    monkeypatch.setattr(
        "murder.llm.harness_control.app_server.bootstrap.AppServerClient",
        _RecordingClient,
    )

    config = tmp_path / "config.toml"
    config.write_text('model_provider = "local"\n', encoding="utf-8")

    connection, _client = await start_app_server_session(
        cwd="/work",
        model="gpt-5.6-sol",
        codex_config_path=config,
    )
    assert connection.desired_model == "gpt-5.6-sol"
    assert connection.desired_model_provider == "local"
    assert thread_starts == [
        {"cwd": "/work", "model": "gpt-5.6-sol", "model_provider": "local"}
    ]

    thread_starts.clear()
    connection, _client = await start_app_server_session(
        cwd="/work",
        model="gpt-5",
        model_provider="openai",
        codex_config_path=config,
    )
    assert connection.desired_model_provider == "openai"
    assert thread_starts == [
        {"cwd": "/work", "model": "gpt-5", "model_provider": "openai"}
    ]

    thread_starts.clear()
    empty = tmp_path / "empty.toml"
    empty.write_text('model = "gpt-5"\n', encoding="utf-8")
    connection, _client = await start_app_server_session(
        cwd="/work",
        model="gpt-5",
        codex_config_path=empty,
    )
    assert connection.desired_model_provider is None
    assert thread_starts == [{"cwd": "/work", "model": "gpt-5"}]
