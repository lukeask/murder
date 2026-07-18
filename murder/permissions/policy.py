"""Side-effect-free permission policy evaluation."""

from __future__ import annotations

from typing import Protocol

from murder.permissions.contracts import (
    AllowOutcome,
    DenyOutcome,
    PermissionContext,
    PolicyOutcome,
    ProposedOperation,
    RequireApprovalOutcome,
)


class PolicyResult:
    def __init__(self, outcome: PolicyOutcome, policy_id: str) -> None:
        self.outcome = outcome
        self.policy_id = policy_id


class PermissionPolicy(Protocol):
    def decide(
        self,
        operation: ProposedOperation,
        context: PermissionContext,
    ) -> PolicyResult: ...


_DESTRUCTIVE_CLIENT_TYPES = frozenset(
    {
        "tool.invoke",
        "file.mutate",
        "network.request",
        "secret.access",
        "git.mutate",
    }
)


class LocalServicePermissionPolicy:
    """Explicit initial policy for the trusted local service boundary."""

    policy_id = "murder.local-service.v1"

    def decide(
        self,
        operation: ProposedOperation,
        context: PermissionContext,
    ) -> PolicyResult:
        del context
        if operation.type == "session.writer.takeover":
            return PolicyResult(
                RequireApprovalOutcome(reason="writer takeover requires explicit approval"),
                self.policy_id,
            )
        if (
            operation.principal.kind in {"user", "client", "llm"}
            and operation.type in _DESTRUCTIVE_CLIENT_TYPES
        ):
            return PolicyResult(
                RequireApprovalOutcome(
                    reason=(
                        f"{operation.type} from {operation.principal.kind} "
                        "requires explicit approval"
                    ),
                ),
                self.policy_id,
            )
        if operation.principal.kind in {"service", "workflow"}:
            return PolicyResult(
                AllowOutcome(reason="trusted local automation principal"),
                self.policy_id,
            )
        if (
            operation.type == "terminal.write"
            and operation.principal.kind in {"user", "client"}
            and operation.lease_fence is not None
        ):
            return PolicyResult(
                AllowOutcome(reason="interactive terminal write is separately holder-fenced"),
                self.policy_id,
            )
        return PolicyResult(
            DenyOutcome(reason="operation is not allowed by the local service policy"),
            self.policy_id,
        )


__all__ = ["LocalServicePermissionPolicy", "PermissionPolicy", "PolicyResult"]
