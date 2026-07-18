"""Validated contracts for live harness sessions and fenced writer leases."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter


class StrEnum(str, Enum):
    """Python 3.10 compatible string enum."""


class ContractModel(BaseModel):
    """Immutable public or persistence contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PrincipalKind(StrEnum):
    USER = "user"
    CLIENT = "client"
    WORKFLOW = "workflow"
    SERVICE = "service"
    REVIEWER = "reviewer"


class PrincipalRef(ContractModel):
    kind: PrincipalKind
    id: str = Field(min_length=1)


class Correlation(ContractModel):
    correlation_id: UUID
    causation_id: UUID | None = None
    trace_id: UUID | None = None


class RequestMeta(ContractModel):
    request_id: UUID
    correlation: Correlation
    expected_revision: int | None = None


class SessionStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"
    WORKING = "working"
    AWAITING_INPUT = "awaiting_input"
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    LOST = "lost"


class SessionTransport(StrEnum):
    TMUX = "tmux"
    APP_SERVER = "app_server"
    SUBPROCESS = "subprocess"


class SessionCapabilities(ContractModel):
    structured_messages: bool = False
    structured_tool_events: bool = False
    structured_approvals: bool = False
    raw_terminal: bool = True
    model_switching: bool = False
    resumable: bool = False
    interruptible: bool = True
    supports_subagents: bool = False


class HarnessSessionRecord(ContractModel):
    session_id: UUID
    agent_id: UUID | None = None
    repository_id: UUID
    harness: str
    model: str | None = None
    effort: str | None = None
    transport: SessionTransport
    transport_ref: str
    status: SessionStatus
    revision: int
    capabilities: SessionCapabilities
    owning_workflow_id: UUID | None = None
    owning_activity_id: UUID | None = None
    started_at: AwareDatetime
    last_observed_at: AwareDatetime | None = None
    stopped_at: AwareDatetime | None = None


class SendStructuredMessage(ContractModel):
    type: Literal["send_structured_message"] = "send_structured_message"
    operation_id: UUID
    text: str
    activity_id: UUID | None = None


class WriteTerminalInput(ContractModel):
    type: Literal["write_terminal_input"] = "write_terminal_input"
    operation_id: UUID
    lease_id: UUID
    fence: int
    encoding: Literal["utf-8", "base64"] = "utf-8"
    data: str


class ResizeTerminal(ContractModel):
    type: Literal["resize_terminal"] = "resize_terminal"
    operation_id: UUID
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)


class InterruptSession(ContractModel):
    type: Literal["interrupt"] = "interrupt"
    operation_id: UUID
    reason: str | None = None


class TerminateSession(ContractModel):
    type: Literal["terminate"] = "terminate"
    operation_id: UUID
    force: bool = False
    reason: str | None = None


SessionCommand = Annotated[
    SendStructuredMessage
    | WriteTerminalInput
    | ResizeTerminal
    | InterruptSession
    | TerminateSession,
    Field(discriminator="type"),
]
SESSION_COMMAND_ADAPTER: TypeAdapter[SessionCommand] = TypeAdapter(SessionCommand)


class WriterMode(StrEnum):
    STRUCTURED = "structured"
    RAW_TERMINAL = "raw_terminal"


class LeaseResource(ContractModel):
    type: Literal["harness_session"] = "harness_session"
    session_id: UUID


class WriterLease(ContractModel):
    lease_id: UUID
    resource: LeaseResource
    holder: PrincipalRef
    mode: WriterMode
    fence: int = Field(ge=1)
    issued_at: AwareDatetime
    renewed_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    revocation_reason: str | None = None


class AcquireWriterLease(ContractModel):
    type: Literal["session.writer.acquire"] = "session.writer.acquire"
    meta: RequestMeta
    session_id: UUID
    mode: WriterMode
    ttl_seconds: int = Field(default=15, ge=3, le=300)
    force: bool = False


class RenewWriterLease(ContractModel):
    type: Literal["session.writer.renew"] = "session.writer.renew"
    meta: RequestMeta
    lease_id: UUID
    fence: int
    ttl_seconds: int = Field(default=15, ge=3, le=300)


class ReleaseWriterLease(ContractModel):
    type: Literal["session.writer.release"] = "session.writer.release"
    meta: RequestMeta
    lease_id: UUID
    fence: int


class WriterLeaseGranted(ContractModel):
    type: Literal["session.writer.granted"] = "session.writer.granted"
    request_id: UUID
    lease: WriterLease


class WriterLeaseDenied(ContractModel):
    type: Literal["session.writer.denied"] = "session.writer.denied"
    request_id: UUID
    current_holder: PrincipalRef | None = None
    current_mode: WriterMode | None = None
    retry_after: AwareDatetime | None = None
    reason: str


WriterLeaseReply = WriterLeaseGranted | WriterLeaseDenied


class SessionCommandReceipt(ContractModel):
    operation_id: UUID
    session_id: UUID
    revision: int
    completed_at: AwareDatetime


def utc_now() -> datetime:
    """Return an aware UTC instant (kept here to share validation semantics)."""

    return datetime.now(timezone.utc)


__all__ = [
    "AcquireWriterLease",
    "ContractModel",
    "Correlation",
    "HarnessSessionRecord",
    "InterruptSession",
    "LeaseResource",
    "PrincipalKind",
    "PrincipalRef",
    "ReleaseWriterLease",
    "RenewWriterLease",
    "RequestMeta",
    "ResizeTerminal",
    "SESSION_COMMAND_ADAPTER",
    "SendStructuredMessage",
    "SessionCapabilities",
    "SessionCommand",
    "SessionCommandReceipt",
    "SessionStatus",
    "SessionTransport",
    "TerminateSession",
    "WriteTerminalInput",
    "WriterLease",
    "WriterLeaseDenied",
    "WriterLeaseGranted",
    "WriterLeaseReply",
    "WriterMode",
]
