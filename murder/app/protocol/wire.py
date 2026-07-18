"""Discriminated application-protocol wire messages."""

from __future__ import annotations

from typing import Annotated, Literal

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
    TerminalStreamGap,
    TerminalTarget,
)

SubscriptionKind = Literal["projections", "notifications", "facts"]


def _subscription_kinds() -> list[SubscriptionKind]:
    return ["projections", "notifications", "facts"]


class ClientHello(ApplicationModel):
    op: Literal["client.hello"] = "client.hello"
    protocol_version: int = APPLICATION_PROTOCOL_VERSION
    client: ClientIdentity


class ServerHello(ApplicationModel):
    op: Literal["server.hello"] = "server.hello"
    protocol_version: int = APPLICATION_PROTOCOL_VERSION
    server_id: str
    queries: list[QueryName] = Field(default_factory=lambda: list(QueryName))
    commands: list[CommandName] = Field(default_factory=lambda: list(CommandName))
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


class TerminalDetachMessage(ApplicationModel):
    op: Literal["terminal.detach"] = "terminal.detach"
    stream_id: str


class TerminalResyncMessage(ApplicationModel):
    op: Literal["terminal.resync"] = "terminal.resync"
    stream_id: str
    after_sequence: int = Field(ge=0)
    reason: Literal["gap", "unsupported_mode"]


class TerminalAttachedMessage(ApplicationModel):
    op: Literal["terminal.attached"] = "terminal.attached"
    stream_id: str
    mode: Literal["replace"] = "replace"


class TerminalFrameMessage(ApplicationModel):
    op: Literal["terminal.frame"] = "terminal.frame"
    stream_id: str
    frame: TerminalFrame


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
    frame: TerminalFrame


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
    | SubscribeMessage
    | UnsubscribeMessage
    | SubscriptionReadyMessage
    | SubscriptionEventMessage
    | TerminalAttachMessage
    | TerminalDetachMessage
    | TerminalResyncMessage
    | TerminalAttachedMessage
    | TerminalFrameMessage
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
    "TerminalResyncMessage",
    "TerminalResyncedMessage",
    "TerminalStreamGapMessage",
    "UnsubscribeMessage",
]
