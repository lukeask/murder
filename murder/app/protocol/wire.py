"""Discriminated application-protocol wire messages."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from murder.app.protocol.common import (
    APPLICATION_PROTOCOL_VERSION,
    ApplicationModel,
    ClientIdentity,
    ErrorBody,
)
from murder.app.protocol.requests import CommandName, CommandRequest, QueryName, QueryRequest
from murder.app.protocol.subscriptions import SubscriptionSnapshot, SubscriptionSpec
from murder.app.protocol.terminal import (
    TerminalChunk,
    TerminalFrame,
    TerminalKeyframe,
    TerminalStreamGap,
    TerminalTarget,
)

SubscriptionKind = Literal["projections", "facts"]


def _subscription_kinds() -> list[SubscriptionKind]:
    return ["projections", "facts"]


class ClientHello(ApplicationModel):
    op: Literal["client.hello"] = "client.hello"
    protocol_version: int = APPLICATION_PROTOCOL_VERSION
    client: ClientIdentity


class ServerHello(ApplicationModel):
    op: Literal["server.hello"] = "server.hello"
    protocol_version: int = APPLICATION_PROTOCOL_VERSION
    server_id: str
    # Populated by the socket from the installed application dispatcher.
    queries: list[QueryName]
    commands: list[CommandName]
    subscriptions: list[SubscriptionKind] = Field(default_factory=_subscription_kinds)
    terminal_streams: bool = True
    fact_cursor: int = Field(default=0, ge=0)
    projection_cursor: int = Field(default=0, ge=0)


class RequestMessage(ApplicationModel):
    op: Literal["request"] = "request"
    request_id: str
    request: QueryRequest | CommandRequest = Field(discriminator="kind")
    timeout_s: float = Field(default=30.0, gt=0, le=300.0)


class ReplyMessage(ApplicationModel):
    op: Literal["reply"] = "reply"
    request_id: str
    result: dict[str, object] = Field(default_factory=dict)


class PlanSeedFailedNotification(ApplicationModel):
    """Best-effort, client-targeted completion notice for asynchronous plan seeding."""

    op: Literal["notification"] = "notification"
    type: Literal["plan.seed_failed"] = "plan.seed_failed"
    plan_name: str
    message: str


class SubscribeMessage(ApplicationModel):
    op: Literal["subscribe"] = "subscribe"
    subscription_id: str
    subscription: SubscriptionSpec


class UnsubscribeMessage(ApplicationModel):
    op: Literal["unsubscribe"] = "unsubscribe"
    subscription_id: str


class SubscriptionReadyMessage(ApplicationModel):
    op: Literal["subscription.ready"] = "subscription.ready"
    subscription_id: str
    snapshot: SubscriptionSnapshot


class SubscriptionEventMessage(ApplicationModel):
    op: Literal["subscription.event"] = "subscription.event"
    subscription_id: str
    cursor: int | None = None
    payload: dict[str, object]


class TerminalAttachMessage(ApplicationModel):
    op: Literal["terminal.attach"] = "terminal.attach"
    stream_id: str
    target: TerminalTarget
    after_sequence: int = Field(default=0, ge=0)
    # Explicit during migration: raw is the native VT stream, while replace
    # preserves capture-pane consumers without overloading either payload.
    mode: Literal["raw", "replace"] = "raw"


class TerminalDetachMessage(ApplicationModel):
    op: Literal["terminal.detach"] = "terminal.detach"
    stream_id: str


class TerminalResyncMessage(ApplicationModel):
    """Request a full keyframe after a gap or a resumed connection."""

    op: Literal["terminal.resync"] = "terminal.resync"
    stream_id: str
    after_sequence: int = Field(ge=0)
    request: Literal["keyframe"] = "keyframe"
    reason: Literal["gap", "reconnect", "unsupported_mode"]


class TerminalInputMessage(ApplicationModel):
    """One ordered, byte-exact terminal-input batch.

    ``data`` is deliberately base64 rather than a JSON string: editor input
    includes controls and arbitrary UTF-8 sequences, neither of which should
    acquire accidental text-normalisation semantics at the wire boundary.
    """

    op: Literal["terminal.input"] = "terminal.input"
    stream_id: str = Field(min_length=1, max_length=200)
    session_id: UUID
    lease_id: UUID
    fence: int = Field(ge=1)
    input_sequence: int = Field(ge=1)
    encoding: Literal["base64"] = "base64"
    data: str = Field(min_length=1, max_length=349_528)


class TerminalInputDetachMessage(ApplicationModel):
    """Release the server-side queue and writer task for an input stream."""

    op: Literal["terminal.input_detach"] = "terminal.input_detach"
    stream_id: str = Field(min_length=1, max_length=200)


class TerminalInputAckMessage(ApplicationModel):
    """Non-critical acknowledgement of the contiguous accepted input prefix."""

    op: Literal["terminal.input_ack"] = "terminal.input_ack"
    stream_id: str
    accepted_through: int = Field(ge=0)


class TerminalAttachedMessage(ApplicationModel):
    op: Literal["terminal.attached"] = "terminal.attached"
    stream_id: str
    mode: Literal["raw", "replace"]


class TerminalFrameMessage(ApplicationModel):
    """Legacy UTF-8 replace frame for existing non-raw capture consumers."""

    op: Literal["terminal.frame"] = "terminal.frame"
    stream_id: str
    frame: TerminalFrame


class TerminalKeyframeMessage(ApplicationModel):
    """An authoritative replace-state for a stable terminal stream."""

    op: Literal["terminal.keyframe"] = "terminal.keyframe"
    stream_id: str
    keyframe: TerminalKeyframe


class TerminalChunkMessage(ApplicationModel):
    op: Literal["terminal.chunk"] = "terminal.chunk"
    stream_id: str
    chunk: TerminalChunk


class TerminalStreamGapMessage(ApplicationModel):
    op: Literal["terminal.gap"] = "terminal.gap"
    stream_id: str
    gap: TerminalStreamGap


class TerminalResyncedMessage(ApplicationModel):
    """Recovery acknowledgement carrying an authoritative full replacement."""

    op: Literal["terminal.resynced"] = "terminal.resynced"
    stream_id: str
    keyframe: TerminalKeyframe


class ErrorMessage(ApplicationModel):
    op: Literal["error"] = "error"
    request_id: str | None = None
    subscription_id: str | None = None
    stream_id: str | None = None
    error: ErrorBody


ApplicationWireMessage = Annotated[
    ClientHello
    | ServerHello
    | RequestMessage
    | ReplyMessage
    | PlanSeedFailedNotification
    | SubscribeMessage
    | UnsubscribeMessage
    | SubscriptionReadyMessage
    | SubscriptionEventMessage
    | TerminalAttachMessage
    | TerminalDetachMessage
    | TerminalResyncMessage
    | TerminalInputMessage
    | TerminalInputDetachMessage
    | TerminalInputAckMessage
    | TerminalAttachedMessage
    | TerminalFrameMessage
    | TerminalKeyframeMessage
    | TerminalChunkMessage
    | TerminalStreamGapMessage
    | TerminalResyncedMessage
    | ErrorMessage,
    Field(discriminator="op"),
]

APPLICATION_WIRE_ADAPTER: TypeAdapter[ApplicationWireMessage] = TypeAdapter(ApplicationWireMessage)

__all__ = [
    "APPLICATION_PROTOCOL_VERSION",
    "APPLICATION_WIRE_ADAPTER",
    "ApplicationWireMessage",
    "ClientHello",
    "ErrorMessage",
    "PlanSeedFailedNotification",
    "ReplyMessage",
    "RequestMessage",
    "ServerHello",
    "SubscribeMessage",
    "SubscriptionEventMessage",
    "SubscriptionReadyMessage",
    "TerminalAttachMessage",
    "TerminalAttachedMessage",
    "TerminalChunkMessage",
    "TerminalDetachMessage",
    "TerminalFrameMessage",
    "TerminalKeyframeMessage",
    "TerminalInputAckMessage",
    "TerminalInputDetachMessage",
    "TerminalInputMessage",
    "TerminalResyncMessage",
    "TerminalResyncedMessage",
    "TerminalStreamGapMessage",
    "UnsubscribeMessage",
]
