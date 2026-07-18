"""Section-17 permission, approval, grant, and authorization contracts."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)


class PermissionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PermissionPrincipal(PermissionModel):
    kind: Literal["user", "client", "workflow", "service", "reviewer", "llm"]
    id: str = Field(min_length=1)


class TerminalWrite(PermissionModel):
    type: Literal["terminal.write"] = "terminal.write"
    operation_id: UUID
    principal: PermissionPrincipal
    session_id: UUID
    encoding: Literal["utf-8", "base64"]
    data_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    lease_id: UUID
    lease_fence: int = Field(ge=1)


class SessionControl(PermissionModel):
    type: Literal["session.control"] = "session.control"
    operation_id: UUID
    principal: PermissionPrincipal
    session_id: UUID
    command: Literal[
        "send_structured_message",
        "resize_terminal",
        "interrupt",
        "terminate",
    ]
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ToolInvocation(PermissionModel):
    type: Literal["tool.invoke"] = "tool.invoke"
    operation_id: UUID
    principal: PermissionPrincipal
    tool: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_id: UUID | None = None
    workflow_id: UUID | None = None
    activity_id: UUID | None = None

    @field_validator("tool")
    @classmethod
    def canonical_tool(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="before")
    @classmethod
    def bind_arguments(cls, value: Any) -> Any:
        return _bind_structured_digest(value, field="arguments")


class FileMutation(PermissionModel):
    type: Literal["file.mutate"] = "file.mutate"
    operation_id: UUID
    principal: PermissionPrincipal
    repository_id: UUID
    path: str = Field(min_length=1)
    action: Literal["create", "write", "append", "delete", "chmod"]
    content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def canonical_path(cls, value: str) -> str:
        return _canonical_repo_path(value)


class NetworkOperation(PermissionModel):
    type: Literal["network.request"] = "network.request"
    operation_id: UUID
    principal: PermissionPrincipal
    destination: str = Field(min_length=1)
    method: str = Field(min_length=1)
    payload_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workflow_id: UUID | None = None
    activity_id: UUID | None = None

    @field_validator("destination")
    @classmethod
    def canonical_destination(cls, value: str) -> str:
        return _canonical_destination(value)

    @field_validator("method")
    @classmethod
    def canonical_method(cls, value: str) -> str:
        return value.strip().upper()


class GitOperation(PermissionModel):
    type: Literal["git.mutate"] = "git.mutate"
    operation_id: UUID
    principal: PermissionPrincipal
    repository_id: UUID
    action: Literal[
        "commit",
        "push",
        "reset",
        "clean",
        "checkout",
        "merge",
        "rebase",
        "worktree_add",
        "worktree_remove",
    ]
    arguments: tuple[str, ...] = ()
    worktree_path: str | None = None
    remote: str | None = None
    ref: str | None = None
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def bind_arguments(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        arguments = tuple(str(item).strip() for item in payload.get("arguments", ()))
        if any(not item or "\x00" in item for item in arguments):
            raise ValueError("Git arguments must be non-empty and control-free")
        payload["arguments"] = arguments
        target = {
            "action": payload.get("action"),
            "arguments": arguments,
            "worktree_path": payload.get("worktree_path"),
            "remote": payload.get("remote"),
            "ref": payload.get("ref"),
        }
        payload["arguments_digest"] = _verified_digest(
            target,
            payload.get("arguments_digest"),
        )
        return payload

    @field_validator("worktree_path")
    @classmethod
    def canonical_worktree_path(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_repo_path(value)

    @model_validator(mode="after")
    def required_action_target(self) -> GitOperation:
        if self.action == "worktree_remove" and self.worktree_path is None:
            raise ValueError("worktree_remove requires worktree_path")
        return self


class SecretAccess(PermissionModel):
    type: Literal["secret.access"] = "secret.access"
    operation_id: UUID
    principal: PermissionPrincipal
    secret_ref: str = Field(min_length=1)
    access: Literal["read", "inject"]
    workflow_id: UUID | None = None
    activity_id: UUID | None = None


class WriterTakeover(PermissionModel):
    type: Literal["session.writer.takeover"] = "session.writer.takeover"
    operation_id: UUID
    principal: PermissionPrincipal
    session_id: UUID
    requested_mode: Literal["structured", "raw_terminal"]
    current_lease_id: UUID
    current_lease_fence: int = Field(ge=1)
    current_holder: PermissionPrincipal
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


ProposedOperation = Annotated[
    TerminalWrite
    | SessionControl
    | ToolInvocation
    | FileMutation
    | NetworkOperation
    | GitOperation
    | SecretAccess
    | WriterTakeover,
    Field(discriminator="type"),
]
PROPOSED_OPERATION_ADAPTER: TypeAdapter[ProposedOperation] = TypeAdapter(ProposedOperation)
NormalizedOperation = ProposedOperation


class PermissionContext(PermissionModel):
    principal: PermissionPrincipal
    profile: dict[str, Any] = Field(default_factory=dict)
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: ProposedOperation
    active_grant_ids: tuple[UUID, ...] = ()
    safety_review_ids: tuple[UUID, ...] = ()
    attributes: dict[str, Any] = Field(default_factory=dict)


def operation_digest(operation: ProposedOperation) -> str:
    payload = PROPOSED_OPERATION_ADAPTER.dump_python(operation, mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AllowDecision(PermissionModel):
    type: Literal["allow"] = "allow"
    reason: str


class DenyDecision(PermissionModel):
    type: Literal["deny"] = "deny"
    reason: str


class RequireApprovalDecision(PermissionModel):
    type: Literal["require_approval"] = "require_approval"
    reason: str
    required_reviewers: tuple[Literal["human", "llm"], ...] = ("human",)
    reviewer_policy: Literal["any", "all", "human_required"] = "human_required"

    @model_validator(mode="after")
    def valid_reviewer_policy(self) -> RequireApprovalDecision:
        _validate_reviewer_policy(self.required_reviewers, self.reviewer_policy)
        return self


class TransformDecision(PermissionModel):
    type: Literal["transform"] = "transform"
    reason: str
    transformed_operation: ProposedOperation


PolicyOutcome = Annotated[
    AllowDecision | DenyDecision | RequireApprovalDecision | TransformDecision,
    Field(discriminator="type"),
]
AllowOutcome = AllowDecision
DenyOutcome = DenyDecision
RequireApprovalOutcome = RequireApprovalDecision


class PermissionDecisionRecord(PermissionModel):
    decision_id: UUID
    operation_id: UUID
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome: PolicyOutcome
    policy_id: str = Field(min_length=1)
    decided_at: AwareDatetime

    @property
    def reason(self) -> str:
        return self.outcome.reason


PolicyDecision = PermissionDecisionRecord


class SafetyReviewEvidence(PermissionModel):
    review_id: UUID
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer: PermissionPrincipal
    verdict: Literal["safe", "unsafe", "uncertain"]
    rationale: str
    findings: tuple[str, ...] = ()
    reviewed_at: AwareDatetime


class GrantScope(PermissionModel):
    repository_ids: tuple[UUID, ...] = ()
    workflow_ids: tuple[UUID, ...] = ()
    activity_ids: tuple[UUID, ...] = ()
    session_ids: tuple[UUID, ...] = ()
    operation_types: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    destinations: tuple[str, ...] = ()
    max_uses: int = Field(default=1, ge=1)
    expires_at: AwareDatetime

    @field_validator("path_prefixes")
    @classmethod
    def canonical_path_prefixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_canonical_repo_path(item) for item in value)

    @field_validator("destinations")
    @classmethod
    def canonical_destinations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_canonical_destination(item) for item in value)


def _canonical_repo_path(value: str) -> str:
    candidate = value.strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or "\x00" in candidate
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("repository path must be normalized, relative, and traversal-free")
    return path.as_posix()


def _canonical_destination(value: str) -> str:
    candidate = value.strip()
    if not candidate or any(character.isspace() for character in candidate):
        raise ValueError("network destination must be normalized and non-empty")
    if "://" not in candidate:
        return candidate.casefold()
    split = urlsplit(candidate)
    if not split.scheme or not split.hostname or split.username or split.password:
        raise ValueError("network destination must have a scheme and host without credentials")
    hostname = split.hostname.casefold()
    port = f":{split.port}" if split.port is not None else ""
    normalized = SplitResult(
        scheme=split.scheme.casefold(),
        netloc=f"{hostname}{port}",
        path=split.path or "/",
        query=split.query,
        fragment="",
    )
    return urlunsplit(normalized)


def _validate_reviewer_policy(
    required_reviewers: tuple[Literal["human", "llm"], ...],
    reviewer_policy: Literal["any", "all", "human_required"],
) -> None:
    if not required_reviewers or len(set(required_reviewers)) != len(required_reviewers):
        raise ValueError("required reviewers must be non-empty and unique")
    if reviewer_policy == "human_required" and "human" not in required_reviewers:
        raise ValueError("human_required policy must allow a human reviewer")


def _bind_structured_digest(value: Any, *, field: str) -> Any:
    if not isinstance(value, dict):
        return value
    payload = dict(value)
    structured = payload.get(field)
    if not isinstance(structured, dict):
        return payload
    payload[f"{field}_digest"] = _verified_digest(
        structured,
        payload.get(f"{field}_digest"),
    )
    return payload


def _verified_digest(value: object, supplied: object) -> str:
    digest = hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if supplied is not None and supplied != digest:
        raise ValueError("structured operation digest does not match its arguments")
    return digest


class ApprovalRequest(PermissionModel):
    approval_id: UUID
    policy_decision_id: UUID
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "approved", "denied", "expired", "cancelled"] = "pending"
    required_reviewers: tuple[Literal["human", "llm"], ...]
    reviewer_policy: Literal["any", "all", "human_required"]
    grant_scope: GrantScope
    requested_at: AwareDatetime
    requested_by: PermissionPrincipal
    workflow_id: UUID | None = None
    workflow_revision: int | None = Field(default=None, ge=0)
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_reviewer_policy(self) -> ApprovalRequest:
        _validate_reviewer_policy(self.required_reviewers, self.reviewer_policy)
        return self


class ApprovalChoice(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    ABSTAIN = "abstain"


class ApprovalDecisionRecord(PermissionModel):
    decision_id: UUID
    approval_id: UUID
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    choice: ApprovalChoice
    reviewer: PermissionPrincipal
    rationale: str = ""
    decided_at: AwareDatetime


ApprovalEvidence = ApprovalDecisionRecord


class PermissionGrant(PermissionModel):
    grant_id: UUID
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_to: PermissionPrincipal
    issued_by: PermissionPrincipal
    scope: GrantScope
    issued_at: AwareDatetime
    approval_id: UUID | None = None
    decision_id: UUID
    revoked_at: AwareDatetime | None = None
    revocation_reason: str | None = None
    uses: int = Field(default=0, ge=0)


AuthorizationGrant = PermissionGrant


class AuthorizationProof(PermissionModel):
    authorization_id: UUID
    grant_id: UUID
    operation_id: UUID
    operation_type: str
    operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject: PermissionPrincipal
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    lease_fence: int | None = Field(default=None, ge=1)


OperationAuthorization = AuthorizationProof


class PermissionOutcome(str, Enum):
    """Compatibility enum; policy records use the closed variants above."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    TRANSFORM = "transform"


__all__ = [
    "AllowDecision",
    "AllowOutcome",
    "ApprovalChoice",
    "ApprovalDecisionRecord",
    "ApprovalEvidence",
    "ApprovalRequest",
    "AuthorizationGrant",
    "AuthorizationProof",
    "DenyDecision",
    "DenyOutcome",
    "FileMutation",
    "GitOperation",
    "GrantScope",
    "NetworkOperation",
    "NormalizedOperation",
    "OperationAuthorization",
    "PROPOSED_OPERATION_ADAPTER",
    "PermissionContext",
    "PermissionDecisionRecord",
    "PermissionGrant",
    "PermissionOutcome",
    "PermissionPrincipal",
    "PolicyDecision",
    "PolicyOutcome",
    "ProposedOperation",
    "RequireApprovalDecision",
    "RequireApprovalOutcome",
    "SafetyReviewEvidence",
    "SecretAccess",
    "SessionControl",
    "TerminalWrite",
    "ToolInvocation",
    "TransformDecision",
    "WriterTakeover",
    "operation_digest",
]
