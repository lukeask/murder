"""Phase 1 acceptance tests for the service-owned application protocol."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from murder.app.protocol.common import APPLICATION_PROTOCOL_VERSION
from murder.app.protocol.requests import (
    CommandName,
    CommandRequest,
    QueryName,
    QueryRequest,
)
from murder.app.protocol.subscriptions import (
    FactSubscription,
    ProjectionSubscription,
    ProjectionTopic,
)
from murder.app.protocol.terminal import TerminalChunk, TerminalFrame, TerminalStreamGap
from murder.app.protocol.wire import (
    APPLICATION_WIRE_ADAPTER,
    ClientHello,
    ErrorMessage,
    ReplyMessage,
    RequestMessage,
    ServerHello,
    SubscribeMessage,
    SubscriptionReadyMessage,
    TerminalAttachMessage,
    TerminalDetachMessage,
    TerminalFrameMessage,
    TerminalResyncedMessage,
    TerminalResyncMessage,
)
from murder.app.service.gateway import (
    COMMAND_TARGETS,
    QUERY_TARGETS,
    ApplicationGateway,
)
from murder.bus.broker import DurableBroker
from murder.bus.protocol import ClientKind
from murder.bus.transport_socket import SocketBusServer, _ClientSession
from murder.facts.contracts import (
    AggregateRef,
    FactActor,
    FactCorrelation,
    ProjectionInputRecord,
    RetainedFactDraft,
)
from murder.facts.log import append_fact
from murder.state.persistence.schema import init_db

ORCHESTRATION_TIMEOUT_S = 3.0


def test_terminal_contracts_distinguish_snapshot_increment_and_gap() -> None:
    frame = TerminalFrame(
        subscription_id="term-1",
        session_id=None,
        legacy_agent_id="crow-1",
        sequence=3,
        captured_at=datetime.now(timezone.utc),
        columns=80,
        rows=24,
        data="full",
    )
    chunk = TerminalChunk(
        subscription_id="term-1",
        session_id=None,
        legacy_agent_id="crow-1",
        sequence=4,
        data="increment",
    )
    gap = TerminalStreamGap(
        subscription_id="term-1",
        session_id=None,
        legacy_agent_id="crow-1",
        expected_sequence=5,
        next_sequence=7,
    )

    assert frame.type == "terminal.frame"
    assert frame.reset is True
    assert chunk.type == "terminal.chunk"
    assert gap.type == "terminal.gap"
    assert gap.snapshot_required is True


class _Broker:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object], float]] = []
        self.published: list[object] = []
        self.fact_replay_calls = 0
        self.legacy_replay_calls = 0
        self.fact_cursor = 0
        self.projection_cursor = 0
        self.projection_cursor_retained = True
        self.projection_replay: tuple[ProjectionInputRecord, ...] = ()

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
        self.legacy_replay_calls += 1
        return []

    async def tail(self, _filter: object = None, *, since_id: int):  # type: ignore[no-untyped-def]
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover

    def fact_watermark(self) -> int:
        return self.fact_cursor

    def is_fact_cursor_retained(self, cursor: int) -> bool:
        return cursor == 0 or cursor <= self.fact_cursor

    def replay_facts(
        self,
        *,
        since_sequence: int,
        kinds: frozenset[str],
        until_sequence: int | None = None,
    ) -> tuple[object, ...]:
        self.fact_replay_calls += 1
        return ()

    async def tail_facts(self, *, since_sequence: int, kinds: frozenset[str]):  # type: ignore[no-untyped-def]
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover

    def projection_watermark(self) -> int:
        return self.projection_cursor

    def is_projection_cursor_retained(self, cursor: int) -> bool:
        return self.projection_cursor_retained and cursor <= self.projection_cursor

    def replay_projection_inputs(self, **_kwargs):  # type: ignore[no-untyped-def]
        return self.projection_replay

    def projection_snapshot(self, projection: str) -> dict[str, object]:
        return {"source": projection}

    async def tail_projection_inputs(self, **_kwargs):  # type: ignore[no-untyped-def]
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_socket_runs_retention_without_legacy_publish_traffic(tmp_path: Path) -> None:
    broker = _Broker()
    retained = asyncio.Event()
    calls: list[str] = []

    def prune_projection_inputs() -> int:
        calls.append("projections")
        raise sqlite3.OperationalError("persistent")

    def prune_retained_facts() -> int:
        calls.append("facts")
        retained.set()
        return 0

    broker.prune_projection_inputs = prune_projection_inputs  # type: ignore[attr-defined]
    broker.prune_retained_facts = prune_retained_facts  # type: ignore[attr-defined]
    server = SocketBusServer(
        broker,  # type: ignore[arg-type]
        run_id="run-1",
        socket_path=tmp_path / "retention.sock",
        retention_interval_s=0.01,
    )
    try:
        await server.start()
    except PermissionError:
        pytest.skip("sandbox forbids Unix-domain socket creation")
    try:
        await asyncio.wait_for(retained.wait(), timeout=1)
        assert calls[:2] == ["projections", "facts"]
        assert broker.published == []
    finally:
        await server.stop()


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

    supplied = {"kind": "service", "id": "forged"}
    await gateway.request(
        CommandRequest(
            name=CommandName.APPROVAL_DECIDE,
            params={
                "approval_id": "a",
                "reviewer": supplied,
            },
        ),
        timeout_s=2,
        authenticated_client_id="tui-7",
    )
    assert broker.requests[-1][0] == "approval.decide"
    assert broker.requests[-1][1]["reviewer"] == {
        "kind": "client",
        "id": "tui-7",
    }
    with pytest.raises(ValueError, match="authenticated"):
        await gateway.request(
            CommandRequest(name=CommandName.APPROVAL_DECIDE),
            timeout_s=2,
        )

    forged_holder = {"kind": "service", "id": "forged"}
    await gateway.request(
        CommandRequest(
            name=CommandName.SESSION_WRITER_ACQUIRE,
            params={
                "session_id": str(uuid4()),
                "mode": "raw_terminal",
                "holder": forged_holder,
            },
        ),
        timeout_s=2,
        authenticated_client_id="tui-9",
    )
    assert broker.requests[-1][0] == "session.writer.acquire"
    assert broker.requests[-1][1]["holder"] == {
        "kind": "client",
        "id": "tui-9",
    }

    await gateway.request(
        CommandRequest(
            name=CommandName.SESSION_WRITER_RENEW,
            params={
                "session_id": str(uuid4()),
                "lease_id": str(uuid4()),
                "fence": 1,
            },
        ),
        timeout_s=2,
    )
    assert broker.requests[-1][0] == "session.writer.renew"
    assert broker.requests[-1][1]["holder"] == {
        "kind": "service",
        "id": "trusted-local",
    }

    await gateway.request(
        QueryRequest(
            name=QueryName.SESSION_WRITER_GET,
            params={"session_id": str(uuid4())},
        ),
        timeout_s=2,
    )
    assert broker.requests[-1][0] == "session.writer.get"


@pytest.mark.asyncio
async def test_application_socket_request_reply_and_no_arbitrary_target(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    broker = _Broker()
    broker.projection_cursor = 2
    broker.projection_replay = (
        ProjectionInputRecord(
            sequence=2,
            input_id=uuid4(),
            projection="activities",
            subject_key="activity-1",
            generation=3,
            created_at=datetime.now(timezone.utc),
        ),
    )
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
        assert "facts" in hello.subscriptions
        assert hello.fact_cursor == 0
        assert hello.projection_cursor == 2

        writer.write(
            (
                SubscribeMessage(
                    subscription_id="facts-1",
                    subscription=FactSubscription(
                        fact_kinds=["workflow.completed"],
                        cursor=0,
                    ),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        fact_ready = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(fact_ready, SubscriptionReadyMessage)
        assert fact_ready.subscription_id == "facts-1"
        assert fact_ready.snapshot.replay == []
        assert broker.fact_replay_calls == 1

        writer.write(
            (
                SubscribeMessage(
                    subscription_id="activities-1",
                    subscription=ProjectionSubscription(
                        topics=[ProjectionTopic.ACTIVITIES],
                    ),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        projection_ready = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(projection_ready, SubscriptionReadyMessage)
        assert projection_ready.snapshot.mode == "cold"
        assert projection_ready.snapshot.snapshots == {
            "activities": {"source": "activities"}
        }
        assert broker.legacy_replay_calls == 0

        writer.write(
            (
                SubscribeMessage(
                    subscription_id="activities-resume",
                    subscription=ProjectionSubscription(
                        topics=[ProjectionTopic.ACTIVITIES],
                        cursor=0,
                    ),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        resumed = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(resumed, SubscriptionReadyMessage)
        assert resumed.snapshot.mode == "resume"
        assert resumed.snapshot.snapshots == {}
        assert resumed.snapshot.replay[0].payload["type"] == "projection.invalidate"
        assert resumed.snapshot.replay[0].payload["source_fact_id"] is None

        broker.projection_cursor_retained = False
        writer.write(
            (
                SubscribeMessage(
                    subscription_id="activities-gap",
                    subscription=ProjectionSubscription(
                        topics=[ProjectionTopic.ACTIVITIES],
                        cursor=1,
                    ),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        gap = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(gap, SubscriptionReadyMessage)
        assert gap.snapshot.mode == "snapshot_fallback"
        assert gap.snapshot.snapshots["activities"] == {"source": "activities"}
        broker.projection_cursor_retained = True

        writer.write(
            (
                SubscribeMessage(
                    subscription_id="empty-projections",
                    subscription=ProjectionSubscription(topics=[]),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        empty_error = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(empty_error, ErrorMessage)
        assert empty_error.subscription_id == "empty-projections"

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


@pytest.mark.asyncio
async def test_fact_tail_gap_reanchors_instead_of_closing_connection(
    tmp_path: Path,
) -> None:
    from murder.bus.broker import ReplayGapError

    broker = _Broker()
    tail_calls = 0

    async def tail_facts(*, since_sequence: int, kinds: frozenset[str]):  # type: ignore[no-untyped-def]
        nonlocal tail_calls
        tail_calls += 1
        if tail_calls == 1:
            broker.fact_cursor = 7
            raise ReplayGapError("fact cursor pruned")
        while True:
            await asyncio.sleep(3600)
        yield  # pragma: no cover

    broker.tail_facts = tail_facts  # type: ignore[assignment]
    socket_path = tmp_path / "fact-gap.sock"
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
                SubscribeMessage(
                    subscription_id="facts-gap",
                    subscription=FactSubscription(),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        initial = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(initial, SubscriptionReadyMessage)
        assert initial.snapshot.mode == "cold"

        recovered = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(recovered, SubscriptionReadyMessage)
        assert recovered.subscription_id == "facts-gap"
        assert recovered.snapshot.mode == "snapshot_fallback"
        assert recovered.snapshot.cursor == 7

        # The connection survived the gap: requests still work.
        writer.write(
            (
                RequestMessage(
                    request_id="q-gap",
                    request=QueryRequest(name=QueryName.HEALTH_GET),
                ).model_dump_json()
                + "\n"
            ).encode()
        )
        await writer.drain()
        reply = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        assert isinstance(reply, ReplyMessage)
        assert reply.request_id == "q-gap"
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_hello_exposes_fact_cursor_watermark(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "facts.db")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    class _NoopBus:
        async def publish(self, event: object) -> None:
            return None

    broker = DurableBroker(_NoopBus(), conn)  # type: ignore[arg-type]
    socket_path = tmp_path / "fact-cursor.sock"
    server = SocketBusServer(broker, run_id="run-facts", socket_path=socket_path)
    try:
        await server.start()
    except PermissionError:
        pytest.skip("sandbox forbids Unix-domain socket creation")

    async def _hello() -> ServerHello:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        writer.write(
            (
                json.dumps(
                    {
                        "op": "client.hello",
                        "protocol_version": APPLICATION_PROTOCOL_VERSION,
                        "client": {"client_id": "tui-facts", "kind": "tui"},
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        message = APPLICATION_WIRE_ADAPTER.validate_json(await reader.readline())
        writer.close()
        await writer.wait_closed()
        assert isinstance(message, ServerHello)
        return message

    try:
        empty = await _hello()
        assert empty.fact_cursor == 0
        assert empty.projection_cursor == 0

        fact, _ = append_fact(
            conn,
            RetainedFactDraft(
                fact_id=uuid4(),
                kind="workflow.completed",
                occurred_at=datetime.now(timezone.utc),
                aggregate=AggregateRef(kind="workflow", id=uuid4(), revision=1),
                actor=FactActor(kind="workflow", id="delivery"),
                correlation=FactCorrelation(
                    correlation_id=uuid4(),
                    causation_id=uuid4(),
                    trace_id=uuid4(),
                ),
                payload={"result": "done"},
            ),
            recorded_at=datetime.now(timezone.utc),
        )

        advanced = await _hello()
        assert advanced.fact_cursor == fact.sequence
        assert advanced.fact_cursor > empty.fact_cursor
        assert advanced.projection_cursor == 0
    finally:
        await server.stop()
        conn.close()


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
        TerminalAttachMessage(
            stream_id="term-1",
            target={"legacy_agent_id": "crow-1"},
        ),
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
    assert all(item.frame.reset for item in frames)
    assert all(item.frame.type == "terminal.frame" for item in frames)
    assert all(item.frame.subscription_id == "term-1" for item in frames)
    assert broker.requests == []


@pytest.mark.asyncio
async def test_terminal_sequence_survives_detach_and_client_resume_cursor() -> None:
    resumed_after = 41
    captures = 0

    async def capture(session_id: str | None) -> str:
        nonlocal captures
        captures += 1
        return f"{session_id}:{captures}"

    server = SocketBusServer(
        _Broker(),  # type: ignore[arg-type]
        run_id="run-1",
        tmux_frame_capture=capture,
        tmux_frame_interval_s=60,
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
        TerminalAttachMessage(
            stream_id="first",
            target={"legacy_agent_id": "crow-1"},
            after_sequence=resumed_after,
        ),
    )
    await asyncio.sleep(0)
    await server._handle_application_message(
        session,
        TerminalDetachMessage(stream_id="first"),
    )
    await server._handle_application_message(
        session,
        TerminalAttachMessage(
            stream_id="second",
            target={"legacy_agent_id": "crow-1"},
            after_sequence=resumed_after + 1,
        ),
    )
    await asyncio.sleep(0)
    await server._handle_application_message(
        session,
        TerminalResyncMessage(
            stream_id="second",
            after_sequence=resumed_after + 1,
            reason="gap",
        ),
    )
    await server._handle_application_message(
        session,
        TerminalDetachMessage(stream_id="second"),
    )

    messages = [
        APPLICATION_WIRE_ADAPTER.validate_json(line)
        for chunk in transport.sent
        for line in chunk.splitlines()
    ]
    frames = [item.frame for item in messages if isinstance(item, TerminalFrameMessage)]
    resyncs = [item.frame for item in messages if isinstance(item, TerminalResyncedMessage)]
    assert frames[0].sequence == resumed_after + 1
    assert frames[-1].sequence == resumed_after + 2
    assert resyncs[-1].sequence == resumed_after + 3
    assert frames[-1].subscription_id == "second"
    assert resyncs[-1].subscription_id == "second"
    assert all(frame.reset for frame in [*frames, *resyncs])
