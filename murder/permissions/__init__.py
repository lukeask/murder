"""Typed permission decisions and side-effect enforcement."""

from murder.permissions.contracts import (
    AllowDecision,
    ApprovalChoice,
    ApprovalDecisionRecord,
    ApprovalRequest,
    AuthorizationProof,
    FileMutation,
    GitOperation,
    GrantScope,
    NetworkOperation,
    PermissionContext,
    PermissionDecisionRecord,
    PermissionGrant,
    PermissionPrincipal,
    ProposedOperation,
    RequireApprovalDecision,
    SafetyReviewEvidence,
    SecretAccess,
    SessionControl,
    TerminalWrite,
    ToolInvocation,
    TransformDecision,
    WriterTakeover,
    operation_digest,
)
from murder.permissions.enforcement import SideEffectEnforcer
from murder.permissions.harness import (
    normalize_harness_permission_request,
    request_harness_permission,
)
from murder.permissions.persistence import PermissionStore
from murder.permissions.policy import LocalServicePermissionPolicy, PolicyResult
from murder.permissions.service import (
    ApprovalRequiredError,
    InvalidAuthorizationError,
    PermissionDeniedError,
    PermissionService,
)
from murder.permissions.session import SessionPermissionAuthorizer

__all__ = [
    "AllowDecision",
    "ApprovalChoice",
    "ApprovalDecisionRecord",
    "ApprovalRequest",
    "ApprovalRequiredError",
    "AuthorizationProof",
    "FileMutation",
    "GitOperation",
    "GrantScope",
    "InvalidAuthorizationError",
    "LocalServicePermissionPolicy",
    "NetworkOperation",
    "PermissionContext",
    "PermissionDecisionRecord",
    "PermissionDeniedError",
    "PermissionGrant",
    "PermissionPrincipal",
    "PermissionService",
    "PermissionStore",
    "PolicyResult",
    "ProposedOperation",
    "RequireApprovalDecision",
    "SafetyReviewEvidence",
    "SecretAccess",
    "SessionControl",
    "SessionPermissionAuthorizer",
    "SideEffectEnforcer",
    "TerminalWrite",
    "ToolInvocation",
    "TransformDecision",
    "WriterTakeover",
    "normalize_harness_permission_request",
    "operation_digest",
    "request_harness_permission",
]
