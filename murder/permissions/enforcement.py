"""Adjacent enforcement wrappers for non-session side-effect executors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from murder.permissions.contracts import (
    AuthorizationProof,
    FileMutation,
    GitOperation,
    NetworkOperation,
    ProposedOperation,
    SecretAccess,
    ToolInvocation,
)
from murder.permissions.service import PermissionService

ResultT = TypeVar("ResultT")
NonSessionOperation = (
    ToolInvocation | FileMutation | NetworkOperation | SecretAccess | GitOperation
)


class SideEffectEnforcer:
    """Validate a typed proof immediately before invoking a concrete executor."""

    def __init__(self, service: PermissionService) -> None:
        self._service = service

    async def execute(
        self,
        operation: NonSessionOperation,
        effect: Callable[[], Awaitable[ResultT]],
        *,
        authorization: AuthorizationProof | None = None,
    ) -> ResultT:
        proof = authorization or self._service.request(operation)
        self._service.enforce(operation, proof)
        # Deliberately no await between enforcement and entering the executor.
        return await effect()


def is_non_session_operation(operation: ProposedOperation) -> bool:
    return isinstance(
        operation,
        (ToolInvocation, FileMutation, NetworkOperation, SecretAccess, GitOperation),
    )


__all__ = ["NonSessionOperation", "SideEffectEnforcer", "is_non_session_operation"]
