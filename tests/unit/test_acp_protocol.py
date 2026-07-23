"""Unit tests for ACP JSON-RPC protocol + connection + client."""

from __future__ import annotations

import asyncio
import json

import pytest

from murder.llm.harness_control.acp import (
    JSONRPC_VERSION,
    AcpClient,
    AcpConnection,
    AcpRpcError,
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
    permission_selected,
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


def test_encode_decode_request_round_trip_includes_jsonrpc() -> None:
    message = RpcRequest(
        id=1,
        method="initialize",
        params={"protocolVersion": 1, "clientInfo": {"name": "murder"}},
    )
    encoded = encode_message(message)
    payload = json.loads(encoded)
    assert payload["jsonrpc"] == JSONRPC_VERSION
    assert payload == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1, "clientInfo": {"name": "murder"}},
    }
    decoded = decode_line(encoded)
    assert is_request(decoded)
    assert decoded == message
    assert message_kind(decoded) == "request"


def test_encode_decode_notification_and_responses() -> None:
    notification = RpcNotification(method="session/cancel", params={"sessionId": "s1"})
    payload = json.loads(encode_message(notification))
    assert payload == {
        "jsonrpc": "2.0",
        "method": "session/cancel",
        "params": {"sessionId": "s1"},
    }
    assert decode_line(encode_message(notification)) == notification
    assert is_notification(notification)

    ok = RpcResponse(id="abc", result={"sessionId": "s1"})
    assert json.loads(encode_message(ok)) == {
        "jsonrpc": "2.0",
        "id": "abc",
        "result": {"sessionId": "s1"},
    }
    assert decode_line(encode_message(ok)) == ok
    assert is_response(ok)

    err = RpcResponse(id=2, error=RpcError(code=-32001, message="overloaded", data={"retry": True}))
    payload = json.loads(encode_message(err))
    assert payload == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32001, "message": "overloaded", "data": {"retry": True}},
    }
    assert decode_line(encode_message(err)) == err


def test_decode_tolerates_missing_jsonrpc() -> None:
    decoded = decode_line(
        '{"method":"session/update","params":{"sessionId":"s1","update":{"sessionUpdate":"agent_message_chunk"}}}'
    )
    assert decoded == RpcNotification(
        method="session/update",
        params={
            "sessionId": "s1",
            "update": {"sessionUpdate": "agent_message_chunk"},
        },
    )


def test_decode_rejects_bad_jsonrpc_version() -> None:
    with pytest.raises(ValueError, match="unsupported jsonrpc"):
        decode_line('{"jsonrpc":"1.0","method":"initialize","id":1}')


def test_decode_rejects_ambiguous_shapes() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        decode_line('{"jsonrpc":"2.0","id":1}')
    with pytest.raises(ValueError, match="empty"):
        decode_line("   ")


def test_connection_requires_argv_or_transport() -> None:
    with pytest.raises(ValueError, match="argv"):
        AcpConnection()


def test_connection_resolves_pending_request_and_queues_events() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AcpConnection(transport=transport)
        await connection.start()

        request_task = asyncio.create_task(
            connection.request("session/new", {"cwd": "/tmp", "mcpServers": []})
        )
        await asyncio.sleep(0)
        assert len(transport.written) == 1
        outbound = json.loads(transport.written[0])
        assert outbound["jsonrpc"] == "2.0"
        assert outbound["method"] == "session/new"
        assert outbound["params"] == {"cwd": "/tmp", "mcpServers": []}
        request_id = outbound["id"]

        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "sess-1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "hi"},
                        },
                    },
                }
            )
        )
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "session/request_permission",
                    "params": {"toolCall": {"toolCallId": "tc1"}},
                }
            )
        )
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": "sess-1"},
                }
            )
        )

        result = await asyncio.wait_for(request_task, timeout=1.0)
        assert result == {"sessionId": "sess-1"}

        notifications = []
        for _ in range(20):
            notifications = connection.drain_notifications()
            if notifications:
                break
            await asyncio.sleep(0.01)
        assert len(notifications) == 1
        assert notifications[0].method == "session/update"

        incoming = await asyncio.wait_for(connection.incoming_requests.get(), timeout=1.0)
        assert incoming == RpcRequest(
            id=99,
            method="session/request_permission",
            params={"toolCall": {"toolCallId": "tc1"}},
        )

        await connection.respond(99, result=permission_selected("allow-once"))
        assert json.loads(transport.written[-1]) == {
            "jsonrpc": "2.0",
            "id": 99,
            "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
        }

        await connection.notify("session/cancel", {"sessionId": "sess-1"})
        assert json.loads(transport.written[-1]) == {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": "sess-1"},
        }

        assert connection.session_id is None
        assert connection.staged_composer_text == ""
        assert connection.prompt_in_flight is False
        connection.staged_composer_text = "hello"
        connection.session_id = "sess-1"
        assert connection.staged_composer_text == "hello"

        await connection.aclose()
        assert transport.closed

    asyncio.run(scenario())


def test_connection_raises_rpc_error_on_error_response() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AcpConnection(transport=transport)
        await connection.start()
        task = asyncio.create_task(
            connection.request("session/prompt", {"sessionId": "s", "prompt": []})
        )
        await asyncio.sleep(0)
        request_id = json.loads(transport.written[0])["id"]
        error_code = -32602
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": error_code, "message": "missing session"},
                }
            )
        )
        with pytest.raises(AcpRpcError, match="missing session") as raised:
            await asyncio.wait_for(task, timeout=1.0)
        assert raised.value.error.code == error_code
        await connection.aclose()

    asyncio.run(scenario())


def test_client_initialize_authenticate_and_session_flow() -> None:  # noqa: PLR0915
    async def scenario() -> None:
        transport = FakeTransport()
        connection = AcpConnection(transport=transport)
        client = AcpClient(connection)
        await connection.start()

        init_task = asyncio.create_task(
            client.initialize(
                client_name="murder",
                client_version="0.0.1",
                client_title="Murder",
            )
        )
        await asyncio.sleep(0)
        init_req = json.loads(transport.written[0])
        assert init_req["jsonrpc"] == "2.0"
        assert init_req["method"] == "initialize"
        assert init_req["params"]["protocolVersion"] == 1
        assert init_req["params"]["clientInfo"] == {
            "name": "murder",
            "version": "0.0.1",
            "title": "Murder",
        }
        assert "clientCapabilities" in init_req["params"]
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": init_req["id"],
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {},
                        "agentInfo": {"name": "cursor", "version": "1"},
                        "authMethods": [{"id": "cursor_login"}],
                    },
                }
            )
        )
        init_result = await asyncio.wait_for(init_task, timeout=1.0)
        assert init_result["protocolVersion"] == 1

        auth_task = asyncio.create_task(client.authenticate("cursor_login"))
        await asyncio.sleep(0)
        auth_req = json.loads(transport.written[-1])
        assert auth_req["method"] == "authenticate"
        assert auth_req["params"] == {"methodId": "cursor_login"}
        transport.push(json.dumps({"jsonrpc": "2.0", "id": auth_req["id"], "result": {}}))
        await asyncio.wait_for(auth_task, timeout=1.0)

        new_task = asyncio.create_task(client.session_new(cwd="/work"))
        await asyncio.sleep(0)
        new_req = json.loads(transport.written[-1])
        assert new_req["method"] == "session/new"
        assert new_req["params"] == {"cwd": "/work", "mcpServers": []}
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": new_req["id"],
                    "result": {"sessionId": "sess-9"},
                }
            )
        )
        new_result = await asyncio.wait_for(new_task, timeout=1.0)
        assert new_result["sessionId"] == "sess-9"
        assert connection.session_id == "sess-9"

        connection.staged_composer_text = "staged prompt"
        assert connection.prompt_in_flight is False
        prompt_task = asyncio.create_task(client.session_prompt("sess-9", "hello"))
        await asyncio.sleep(0)
        assert connection.prompt_in_flight is True
        prompt_req = json.loads(transport.written[-1])
        assert prompt_req["method"] == "session/prompt"
        assert prompt_req["params"] == {
            "sessionId": "sess-9",
            "prompt": [{"type": "text", "text": "hello"}],
        }
        transport.push(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": prompt_req["id"],
                    "result": {"stopReason": "end_turn"},
                }
            )
        )
        prompt_result = await asyncio.wait_for(prompt_task, timeout=1.0)
        assert prompt_result["stopReason"] == "end_turn"
        assert connection.prompt_in_flight is False
        assert connection.staged_composer_text == "staged prompt"

        await client.session_cancel("sess-9")
        assert json.loads(transport.written[-1]) == {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {"sessionId": "sess-9"},
        }

        await client.respond_permission(42, option_id="reject-once")
        assert json.loads(transport.written[-1]) == {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"outcome": {"outcome": "selected", "optionId": "reject-once"}},
        }

        await connection.aclose()

    asyncio.run(scenario())
