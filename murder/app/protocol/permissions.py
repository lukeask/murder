"""Typed approval and permission application contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from murder.app.protocol.common import ApplicationModel
from murder.permissions.contracts import (
    ApprovalChoice,
    ApprovalDecisionRecord,
    ApprovalRequest,
    AuthorizationProof,
    PermissionGrant,
    PermissionPrincipal,
)


class ListApprovalsParams(ApplicationModel):
    status: Literal[
        "pending",
        "approved",
        "denied",
        "expired",
        "cancelled",
    ] | None = None
    workflow_id: UUID | None = None


class ListApprovalsResult(ApplicationModel):
    approvals: list[ApprovalRequest]


class GetApprovalParams(ApplicationModel):
    approval_id: UUID


class GetApprovalResult(ApplicationModel):
    ok: bool
    approval: ApprovalRequest | None = None
    error: Literal["not_found"] | None = None


class DecideApprovalParams(ApplicationModel):
    approval_id: UUID
    workflow_id: UUID | None = None
    expected_workflow_revision: int | None = Field(default=None, ge=0)
    expected_operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    choice: ApprovalChoice
    rationale: str = Field(min_length=1, max_length=4000)
    reviewer: PermissionPrincipal | None = None
    request_id: UUID | None = None


class DecideApprovalResult(ApplicationModel):
    decision: ApprovalDecisionRecord
    grant: PermissionGrant | None = None
    authorization: AuthorizationProof | None = None


class ListPermissionsParams(ApplicationModel):
    """Empty params object for ``permissions.list``."""


class ListPermissionsResult(ApplicationModel):
    grants: list[PermissionGrant]


__all__ = [
    "DecideApprovalParams",
    "DecideApprovalResult",
    "GetApprovalParams",
    "GetApprovalResult",
    "ListApprovalsParams",
    "ListApprovalsResult",
    "ListPermissionsParams",
    "ListPermissionsResult",
]
