"""Phase 1 acceptance tests for the service-owned application protocol."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION
from murder.app.protocol.requests import (
    CommandName,
    CommandRequest,
    QueryName,
    QueryRequest,
)
from murder.app.protocol.wire import (
    APPLICATION_WIRE_ADAPTER,
    ClientHello,
    ErrorMessage,
    ReplyMessage,
    RequestMessage,
    ServerHello,
    TerminalAttachMessage,
    TerminalDetachMessage,
    TerminalFrameMessage,
)
from murder.app.service.gateway import (
    COMMAND_TARGETS,
    QUERY_TARGETS,
    ApplicationGateway,
)
from murder.bus.protocol import ClientKind
from murder.bus.transport_socket import SocketBusServer, _ClientSession

ORCHESTRATION_TIMEOUT_S = 3.0


class _Broker:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object], float]] = []
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)

    async def request(
        self,
        target: str,
        body: dict[str, object],
        *,
        timeout_s: float,
    ) -> dict[str, object]:
        self.requests.append((target, body, timeout_s))
        return {"target": target}

    def watermark(self) -> int:
        return 0

    def replay(
        self,
        _filter: object = None,
        *,
        since_id: int,
        until_id: int | None = None,
    ) -> list[tuple[int, object]]:
        return []

    async def tail(self, _filter: object = None, *, since_id: int):  # type: ignore[no-untyped-def]
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover


def test_application_wire_is_closed_and_rejects_legacy_bus_ops() -> None:
    hello = APPLICATION_WIRE_ADAPTER.validate_python(
        {
            "op": "client.hello",
            "protocol_version": APPLICATION_PROTOCOL_VERSION,
            "client": {"client_id": "tui-1", "kind": "tui"},
        }
    )
    assert isinstance(hello, ClientHello)

    for legacy in ("rpc", "pub", "sub", "hydrate"):
        with pytest.raises(ValidationError):
            APPLICATION_WIRE_ADAPTER.validate_python({"op": legacy})

    with pytest.raises(ValidationError):
        APPLICATION_WIRE_ADAPTER.validate_python(
            {
                "op": "client.hello",
                "protocol_version": APPLICATION_PROTOCOL_VERSION,
                "client": {"client_id": "tui-1", "kind": "tui"},
                "unexpected": True,
            }
        )


def test_every_closed_request_has_an_adapter() -> None:
    assert set(QUERY_TARGETS) == set(QueryName)
    assert set(COMMAND_TARGETS) | {CommandName.ORCHESTRATION_EXECUTE} == set(CommandName)


@pytest.mark.asyncio
async def test_gateway_maps_closed_queries_and_hides_worker_address() -> None:
    broker = _Broker()
    gateway = ApplicationGateway(broker)  # type: ignore[arg-type]

    result = await gateway.request(
        QueryRequest(name=QueryName.ROSTER_GET),
        timeout_s=2,
    )
    assert result == {"target": "state.crow_snapshot"}
    assert broker.requests[-1] == ("state.crow_snapshot", {}, 2)

    await gateway.request(
        CommandRequest(
            name=CommandName.ORCHESTRATION_EXECUTE,
            params={"kind": "agent.message", "payload": {"agent_id": "a", "message": "hi"}},
        ),
        timeout_s=ORCHESTRATION_TIMEOUT_S,
    )
    target, params, timeout = broker.requests[-1]
    assert target == "command.submit"
    assert params["target_worker"] == "orchestrator"
    assert timeout == ORCHESTRATION_TIMEOUT_S

    with pytest.raises(ValueError):
        await gateway.request(
            CommandRequest(
                name=CommandName.ORCHESTRATION_EXECUTE,
                params={"kind": "worker.delete_everything", "payload": {}},
            ),
            timeout_s=ORCHESTRATION_TIMEOUT_S,
        )


@pytest.mark.asyncio
async def test_application_socket_request_reply_and_no_arbitrary_target(tmp_path: Path) -> None:
    broker = _Broker()
    socket_path = tmp_path / "service.sock"
    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-1",
        socket_path=socket_path,
    )
    try:
        await server.start()
    except PermissionError:
        pytest.skip("sandbox forbids Unix-domain socket creation")
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            (
                json.dumps(
                    {
                        "op": "client.hello",
                        "protocol_version": APPLICATION_PROTOCOL_VERSION,
                        "client": {"client_id": "tui-1", "kind": "tui"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        hello = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(hello, ServerHello)

        writer.write(
            (
                RequestMessage(
                    request_id="q-1",
                    request=QueryRequest(name=QueryName.HEALTH_GET),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        reply = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(reply, ReplyMessage)
        assert reply.request_id == "q-1"
        assert reply.result == {"target": "health.ping"}
        assert broker.requests == [("health.ping", {}, 30.0)]

        # The application adapter rejects an old arbitrary RPC frame instead
        # of forwarding its attacker-chosen target to DurableBroker.
        writer.write(
            b'{"op":"rpc","correlation_id":"x","args":{"target":"secret.dump","body":{}}}\n'
        )
        await writer.drain()
        error = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(error, ErrorMessage)
        assert broker.requests == [("health.ping", {}, 30.0)]
        writer.close()
        await writer.wait_closed()

        legacy_reader, legacy_writer = await asyncio.open_unix_connection(str(socket_path))
        legacy_writer.write(
            b'{"op":"hello","schema_version":5,"correlation_id":"legacy",'
            b'"body":{"protocol_version":5,"client_kind":"tui","client_id":"old-tui"}}\n'
        )
        await legacy_writer.drain()
        legacy_error = json.loads(await legacy_reader.readline())
        assert legacy_error["op"] == "err"
        assert legacy_error["body"]["code"] == "application_protocol_required"
        legacy_writer.close()
        await legacy_writer.wait_closed()
    finally:
        await server.stop()


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        return


@pytest.mark.asyncio
async def test_terminal_stream_is_independent_and_detaches() -> None:
    broker = _Broker()
    captures = 0

    async def capture(session_id: str | None) -> str:
        nonlocal captures
        captures += 1
        return f"{session_id}:{captures}"

    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-1",
        tmux_frame_capture=capture,
        tmux_frame_interval_s=0,
    )
    transport = _RecordingTransport()
    session = _ClientSession(
        client_id="tui-1",
        kind=ClientKind.TUI,
        transport=transport,  # type: ignore[arg-type]
        application=True,
    )
    await server._handle_application_message(
        session,
        TerminalAttachMessage(stream_id="term-1", target={"session_id": "crow-1"}),
    )
    for _ in range(5):
        await asyncio.sleep(0)
    await server._handle_application_message(
        session,
        TerminalDetachMessage(stream_id="term-1"),
    )
    at_detach = captures
    await asyncio.sleep(0)
    assert captures == at_detach
    assert not session.application_tasks

    messages = [
        APPLICATION_WIRE_ADAPTER.validate_json(line)
        for chunk in transport.sent
        for line in chunk.splitlines()
    ]
    frames = [item for item in messages if isinstance(item, TerminalFrameMessage)]
    assert frames
    assert [item.frame.sequence for item in frames] == list(range(1, len(frames) + 1))
    assert all(item.frame.mode == "replace" for item in frames)
    assert broker.requests == []
