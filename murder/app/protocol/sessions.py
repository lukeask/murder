"""Typed session writer-lease and session-command application contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from murder.app.protocol.common import ApplicationModel
from murder.contracts.common import Principal
from murder.permissions.contracts import AuthorizationProof
from murder.runtime.sessions.contracts import (
    InterruptSession,
    ResizeTerminal,
    SendStructuredMessage,
    SessionCommandReceipt,
    TerminateSession,
    WriterLease,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)

SessionCommandWire = Annotated[
    SendStructuredMessage
    | WriteTerminalInput
    | ResizeTerminal
    | InterruptSession
    | TerminateSession,
    Field(discriminator="type"),
]

PrincipalRef = Principal


class GetWriterLeaseParams(ApplicationModel):
    session_id: UUID


class GetWriterLeaseResult(ApplicationModel):
    ok: bool
    lease: WriterLease | None = None
    error: Literal["not_found"] | None = None


class AcquireWriterLeaseParams(ApplicationModel):
    session_id: UUID
    mode: WriterMode
    ttl_seconds: int = Field(default=15, ge=3, le=300)
    force: bool = False
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef | None = None


class RenewWriterLeaseParams(ApplicationModel):
    session_id: UUID
    lease_id: UUID
    fence: int = Field(ge=1)
    ttl_seconds: int = Field(default=15, ge=3, le=300)
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef | None = None


class ReleaseWriterLeaseParams(ApplicationModel):
    session_id: UUID
    lease_id: UUID
    fence: int = Field(ge=1)
    request_id: UUID | None = None
    expected_revision: int | None = None
    holder: PrincipalRef | None = None
    reason: str | None = None


WriterLeaseResult = WriterLeaseGranted | WriterLeaseDenied


class ExecuteSessionCommandParams(ApplicationModel):
    """Execute one closed session command (raw input, resize, interrupt, …)."""

    session_id: UUID
    command: SessionCommandWire
    expected_revision: int | None = None
    authorization: AuthorizationProof | None = None
    request_id: UUID | None = None
    principal: PrincipalRef | None = None


class ExecuteSessionCommandResult(ApplicationModel):
    receipt: SessionCommandReceipt


__all__ = [
    "AcquireWriterLeaseParams",
    "ExecuteSessionCommandParams",
    "ExecuteSessionCommandResult",
    "GetWriterLeaseParams",
    "GetWriterLeaseResult",
    "PrincipalRef",
    "ReleaseWriterLeaseParams",
    "RenewWriterLeaseParams",
    "SessionCommandWire",
    "WriterLeaseResult",
]
