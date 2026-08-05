"""Shared application-protocol contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StrEnum(str, Enum):
    """Python-3.10-compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


# ``terminal.input`` is a new client→server stream message.  It is not
# compatible with a v2 peer which could otherwise silently ignore it.
APPLICATION_PROTOCOL_VERSION = 4

# Single-daemon listener. All clients attach here; repo identity is path-scoped
# on the WebSocket URL (Phase 3). Not ephemeral — CLI probes this port instead
# of spawning a second process when a live daemon already holds it.
DAEMON_WEBSOCKET_HOST = "127.0.0.1"
DAEMON_WEBSOCKET_PORT = 62077


class ClientKind(StrEnum):
    TUI = "tui"
    WEB = "web"
    CLI = "cli"


class ErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    VERSION_MISMATCH = "version_mismatch"
    UNSUPPORTED_REQUEST = "unsupported_request"
    REQUEST_FAILED = "request_failed"
    UNSUPPORTED_SUBSCRIPTION = "unsupported_subscription"
    STREAM_FAILED = "stream_failed"


class ApplicationModel(BaseModel):
    """Strict, forward-compatible base for public wire models."""

    model_config = ConfigDict(extra="forbid")


class ClientIdentity(ApplicationModel):
    client_id: str = Field(min_length=1, max_length=200)
    kind: ClientKind


class ErrorBody(ApplicationModel):
    code: ErrorCode
    message: str
    details: dict[str, object] = Field(default_factory=dict)
