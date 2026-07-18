"""Shared application-protocol contracts."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StrEnum(str, Enum):
    """Python-3.10-compatible string enum."""

    def __str__(self) -> str:
        return str.__str__(self)


APPLICATION_PROTOCOL_VERSION = 1


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
