"""Unit tests for Codex app-server JSON-RPC protocol + connection."""

from __future__ import annotations

import asyncio
import json

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
            json.dumps(
                {"method": "thread/started", "params": {"thread": {"id": "th1"}}}
            )
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

        start_task = asyncio.create_task(client.thread_start(cwd="/work", model="gpt-5"))
        await asyncio.sleep(0)
        start_req = json.loads(transport.written[-1])
        assert start_req["method"] == "thread/start"
        assert start_req["params"] == {"cwd": "/work", "model": "gpt-5"}
        transport.push(
            json.dumps(
                {
                    "id": start_req["id"],
                    "result": {"thread": {"id": "thread-1"}, "model": "gpt-5"},
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
