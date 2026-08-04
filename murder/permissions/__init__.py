"""Typed permission decisions and lazily loaded side-effect enforcement."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from murder.contracts.common import Principal
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

if TYPE_CHECKING:
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


_LAZY_EXPORTS = {
    "ApprovalRequiredError": ("murder.permissions.service", "ApprovalRequiredError"),
    "InvalidAuthorizationError": ("murder.permissions.service", "InvalidAuthorizationError"),
    "LocalServicePermissionPolicy": (
        "murder.permissions.policy",
        "LocalServicePermissionPolicy",
    ),
    "PermissionDeniedError": ("murder.permissions.service", "PermissionDeniedError"),
    "PermissionService": ("murder.permissions.service", "PermissionService"),
    "PermissionStore": ("murder.permissions.persistence", "PermissionStore"),
    "PolicyResult": ("murder.permissions.policy", "PolicyResult"),
    "SessionPermissionAuthorizer": (
        "murder.permissions.session",
        "SessionPermissionAuthorizer",
    ),
    "SideEffectEnforcer": ("murder.permissions.enforcement", "SideEffectEnforcer"),
    "normalize_harness_permission_request": (
        "murder.permissions.harness",
        "normalize_harness_permission_request",
    ),
    "request_harness_permission": (
        "murder.permissions.harness",
        "request_harness_permission",
    ),
}


def __getattr__(name: str) -> Any:
    """Load service-layer exports only when callers request them.

    Protocol/schema imports use ``murder.permissions.contracts`` and should not initialize the
    persistence stack (or require Turso) inside an isolated package build.
    """
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value

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
    "Principal",
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
