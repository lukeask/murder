"""Contract tests for `murder.bus.protocol`.

Verifies that every BusEvent and WireMessage subtype round-trips through
its discriminated-union TypeAdapter, that EventFilter matches each
filter dimension, and that the protocol version handshake catches drift.

If this test fails, both branches of the worker-bus refactor will hit
the same break — fix the contract before either branch ships code that
depends on it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from murder.bus.protocol import (
    BUS_EVENT_ADAPTER,
    PROTOCOL_VERSION,
    WIRE_MESSAGE_ADAPTER,
    AckBody,
    AckMessage,
    AgentStatus,
    ClientKind,
    CommandEvent,
    CommandStatus,
    Entity,
    ErrBody,
    ErrMessage,
    ErrorEvent,
    EscalationEvent,
    EventFilter,
    HeartbeatEvent,
    HelloBody,
    HelloMessage,
    PRESENCE_USER_KINDS,
    PresenceEvent,
    PresenceState,
    PubMessage,
    QuestionEvent,
    Role,
    RpcArgs,
    RpcMessage,
    StateSnapshotEvent,
    StatusChangeEvent,
    SubArgs,
    SubMessage,
    SummaryEvent,
    TicketStatus,
    WakeBody,
    WakeMessage,
)


# === Sample factories =========================================================

def _heartbeat() -> HeartbeatEvent:
    return HeartbeatEvent(run_id="r1", agent_id="a1", role=Role.COLLABORATOR, state="progressing", summary="ok")


def _summary() -> SummaryEvent:
    return SummaryEvent(run_id="r1", agent_id="a1", role=Role.NOTETAKER, text="hi")


def _question() -> QuestionEvent:
    return QuestionEvent(run_id="r1", agent_id="a1", role=Role.CROW, question="?", crow_session="s")


def _escalation() -> EscalationEvent:
    return EscalationEvent(run_id="r1", agent_id="a1", role=Role.SENTINEL, to="user", reason="x")


def _status_change() -> StatusChangeEvent:
    return StatusChangeEvent(
        run_id="r1", agent_id="a1", role=Role.CROW_HANDLER,
        entity="ticket", entity_id="t1",
        from_status=TicketStatus.READY.value, to_status=TicketStatus.IN_PROGRESS.value,
    )


def _error() -> ErrorEvent:
    return ErrorEvent(run_id="r1", agent_id="a1", role=Role.CROW, message="boom")


def _command() -> CommandEvent:
    return CommandEvent(
        run_id="r1",
        target_worker="collaborator",
        kind="collaborator.chat_send",
        payload={"text": "hi"},
        correlation_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        status=CommandStatus.PENDING,
    )


def _state_snapshot() -> StateSnapshotEvent:
    return StateSnapshotEvent(
        run_id="r1", entity=Entity.TICKET, key="t007", entity_version=3,
    )


def _presence() -> PresenceEvent:
    return PresenceEvent(
        run_id="r1",
        state=PresenceState.ATTENDED,
        user_count=1,
        kinds={"tui": 1},
        version=1,
    )


BUS_EVENT_SAMPLES = [
    _heartbeat, _summary, _question, _escalation, _status_change, _error,
    _command, _state_snapshot, _presence,
]


# === BusEvent round-trip ======================================================

@pytest.mark.parametrize("factory", BUS_EVENT_SAMPLES, ids=lambda f: f.__name__)
def test_bus_event_roundtrip(factory):
    instance = factory()
    blob = instance.model_dump_json()
    rt = BUS_EVENT_ADAPTER.validate_json(blob)
    assert rt == instance
    assert type(rt) is type(instance)


def test_bus_event_discriminator_dispatches_correctly():
    cmd = _command()
    blob = cmd.model_dump_json()
    rt = BUS_EVENT_ADAPTER.validate_json(blob)
    assert isinstance(rt, CommandEvent)
    assert rt.kind == "collaborator.chat_send"


def test_bus_event_rejects_unknown_type():
    with pytest.raises(Exception):  # Pydantic ValidationError
        BUS_EVENT_ADAPTER.validate_python({"type": "unknown_type", "run_id": "r1"})


# === WireMessage round-trip ===================================================

def _hello_msg() -> HelloMessage:
    return HelloMessage(
        correlation_id="c1",
        body=HelloBody(
            protocol_version=PROTOCOL_VERSION,
            client_kind=ClientKind.TUI,
            client_id="cli-1",
        ),
    )


def _pub_msg() -> PubMessage:
    return PubMessage(correlation_id="c2", event=_command())


def _sub_msg() -> SubMessage:
    return SubMessage(
        correlation_id="c3",
        args=SubArgs(filter=EventFilter(role=Role.CROW), since_id=42),
    )


def _rpc_msg() -> RpcMessage:
    return RpcMessage(
        correlation_id="c4",
        args=RpcArgs(target="scheduler", body={"harness": "claude_code"}, timeout_s=10.0),
    )


def _ack_msg() -> AckMessage:
    return AckMessage(
        correlation_id="c5",
        body=AckBody(kind="replay_done", watermark=1234),
    )


def _err_msg() -> ErrMessage:
    return ErrMessage(
        correlation_id="c6",
        body=ErrBody(code="protocol_version_mismatch", message="server=2 client=1"),
    )


def _wake_msg() -> WakeMessage:
    return WakeMessage(
        correlation_id="",
        body=WakeBody(client_id="cli-1", reason="connect", fresh_state_hints=[Entity.TICKET]),
    )


WIRE_MESSAGE_SAMPLES = [
    _hello_msg, _pub_msg, _sub_msg, _rpc_msg, _ack_msg, _err_msg, _wake_msg,
]


@pytest.mark.parametrize("factory", WIRE_MESSAGE_SAMPLES, ids=lambda f: f.__name__)
def test_wire_message_roundtrip(factory):
    instance = factory()
    blob = instance.model_dump_json()
    rt = WIRE_MESSAGE_ADAPTER.validate_json(blob)
    assert rt == instance
    assert type(rt) is type(instance)


def test_wire_message_carries_schema_version():
    msg = _pub_msg()
    assert msg.schema_version == PROTOCOL_VERSION


def test_pub_message_event_discriminator_survives_envelope():
    """The discriminated BusEvent inside the PubMessage envelope must
    still dispatch to the right concrete class after wire round-trip."""
    inner = _state_snapshot()
    msg = PubMessage(correlation_id="c", event=inner)
    blob = msg.model_dump_json()
    rt = WIRE_MESSAGE_ADAPTER.validate_json(blob)
    assert isinstance(rt, PubMessage)
    assert isinstance(rt.event, StateSnapshotEvent)
    assert rt.event.entity == Entity.TICKET


# === EventFilter ==============================================================

def test_event_filter_empty_matches_anything():
    f = EventFilter()
    assert f.matches(_heartbeat())
    assert f.matches(_command())
    assert f.matches(_presence())


def test_event_filter_by_role():
    f = EventFilter(role=Role.CROW)
    assert f.matches(_question())
    assert not f.matches(_heartbeat())  # collaborator


def test_event_filter_by_type():
    f = EventFilter(type="command")
    assert f.matches(_command())
    assert not f.matches(_heartbeat())


def test_event_filter_by_entity():
    f = EventFilter(entity=Entity.TICKET)
    assert f.matches(_state_snapshot())
    snap_plan = StateSnapshotEvent(run_id="r", entity=Entity.PLAN, key="p")
    assert not f.matches(snap_plan)


def test_event_filter_by_target_worker():
    f = EventFilter(target_worker="collaborator")
    assert f.matches(_command())
    other = _command()
    other.target_worker = "scheduler"
    assert not f.matches(other)


def test_event_filter_by_kind():
    f = EventFilter(kind="collaborator.chat_send")
    assert f.matches(_command())


def test_event_filter_compose_and_semantics():
    """All set fields must match."""
    f = EventFilter(role=Role.CROW, type="question")
    assert f.matches(_question())
    assert not f.matches(_command())  # right type? no — different
    assert not f.matches(_heartbeat())  # right role? no


# === Protocol invariants ======================================================

def test_protocol_version_is_int_and_set():
    assert isinstance(PROTOCOL_VERSION, int)
    assert PROTOCOL_VERSION >= 1


def test_presence_user_kinds_excludes_internal():
    assert ClientKind.TUI in PRESENCE_USER_KINDS
    assert ClientKind.WEB in PRESENCE_USER_KINDS
    assert ClientKind.WORKER not in PRESENCE_USER_KINDS
    assert ClientKind.CLI_EPHEMERAL not in PRESENCE_USER_KINDS


def test_presence_event_version_is_required():
    """Subscribers rely on monotonic version for ordering safety. The
    field must be required, not defaulted."""
    with pytest.raises(Exception):
        PresenceEvent(run_id="r", state=PresenceState.HEADLESS)  # missing version


def test_command_event_kind_is_str_not_literal():
    """`kind` is intentionally `str`, not `Literal`, so new command kinds
    can be added without coordinated protocol bumps. This test exists to
    keep that property obvious to future readers."""
    cmd = _command()
    cmd.kind = "scheduler.new_kind_we_invented_today"
    blob = cmd.model_dump_json()
    rt = BUS_EVENT_ADAPTER.validate_json(blob)
    assert rt.kind == "scheduler.new_kind_we_invented_today"


def test_entity_is_closed_enum():
    """Closed by design — see Entity docstring. If this fails, you added
    a value without bumping PROTOCOL_VERSION; revert and bump first."""
    expected = {"ticket", "agent", "plan", "note", "escalation", "queue_row", "sentinel_state"}
    actual = {e.value for e in Entity}
    assert actual == expected, (
        "Entity enum changed without PROTOCOL_VERSION bump. "
        "If intentional, update PROTOCOL_VERSION in protocol.py and this test."
    )
