"""Minimal bridge from harness permission observations to ``murder.permissions``.

Seam (wired from ``StructuredDecisionRouter``):

* **Decision-record path** — Harness adapters surface a ``PermissionRequestState``.
  ``StructuredDecisionRouter`` records a durable, identity-bound decision,
  a human/policy later records a response, and verified control
  answers the dialog via ``answer_verified_permission``. That path never
  executes the underlying tool/file/network side effect; it only resolves the
  UI prompt.
* **Permissions path** — The same observation also calls
  ``request_harness_permission`` so ``PermissionService`` persists Allow /
  RequireApproval / Deny evidence. A later harness answer resolves any pending
  approval and may issue ``AuthorizationProof``. Approvals do not execute
  operations.

Harness CLI adapters themselves do not invoke tools or mutate files; the
external harness process does. ``SideEffectEnforcer`` therefore belongs at
Murder-owned executors (sessions, worktrees, …), not inside adapter parsers.
Callers that perform side effects must still consume the proof at enforcement
time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID, uuid4

from murder.permissions.contracts import (
    AuthorizationProof,
    FileMutation,
    PermissionPrincipal,
    ToolInvocation,
)
from murder.permissions.service import PermissionService

_FILE_RISKS = frozenset({"write", "edit", "file"})
_FILE_TOOLS = frozenset({"edit", "write", "write_file", "apply_patch", "strreplace"})


class HarnessPermissionLike(Protocol):
    """Structural subset of harness ``PermissionRequestState`` / router payloads."""

    @property
    def tool_name(self) -> str | None: ...

    @property
    def command(self) -> str | None: ...

    @property
    def description(self) -> str | None: ...

    @property
    def risk_attributes(self) -> frozenset[str] | tuple[str, ...] | list[str]: ...

    @property
    def request_id_hint(self) -> str | None: ...


def normalize_harness_permission_request(
    request: HarnessPermissionLike | Mapping[str, Any],
    *,
    principal: PermissionPrincipal,
    operation_id: UUID | None = None,
    repository_id: UUID | None = None,
) -> ToolInvocation | FileMutation:
    """Map a harness permission observation to the closest ``ProposedOperation``.

    File mutations require a repository id and a traversal-free relative path
    extractable from the command; otherwise the request becomes a tool
    invocation (the common shell/tool dialog case).
    """

    fields = _fields(request)
    tool = (fields["tool_name"] or "unknown").strip() or "unknown"
    command = (fields["command"] or "").strip() or None
    description = (fields["description"] or "").strip() or None
    risks = frozenset(str(item).strip().casefold() for item in fields["risk_attributes"] if item)
    op_id = operation_id or uuid4()

    if _looks_like_file_mutation(tool, risks) and repository_id is not None:
        path = _relative_path_candidate(command)
        if path is not None:
            return FileMutation(
                operation_id=op_id,
                principal=principal,
                repository_id=repository_id,
                path=path,
                action="write",
                content_digest=None,
            )

    arguments: dict[str, Any] = {}
    if command is not None:
        arguments["command"] = command
    if description is not None:
        arguments["description"] = description
    if fields["request_id_hint"]:
        arguments["request_id_hint"] = fields["request_id_hint"]
    if risks:
        arguments["risk_attributes"] = sorted(risks)
    return ToolInvocation(
        operation_id=op_id,
        principal=principal,
        tool=tool,
        arguments=arguments,
    )


def request_harness_permission(
    service: PermissionService,
    request: HarnessPermissionLike | Mapping[str, Any],
    *,
    principal: PermissionPrincipal,
    operation_id: UUID | None = None,
    repository_id: UUID | None = None,
) -> AuthorizationProof:
    """Normalize then ``PermissionService.request``.

    Returns an ``AuthorizationProof`` on allow. Raises ``ApprovalRequiredError``
    (with persisted approval request) or ``PermissionDeniedError`` otherwise.
    Does not answer the harness dialog and does not execute the operation.
    """

    return service.request(
        normalize_harness_permission_request(
            request,
            principal=principal,
            operation_id=operation_id,
            repository_id=repository_id,
        )
    )


def _fields(request: HarnessPermissionLike | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(request, Mapping):
        risks = request.get("risk_attributes", ())
        return {
            "tool_name": request.get("tool_name"),
            "command": request.get("command"),
            "description": request.get("description"),
            "risk_attributes": risks if risks is not None else (),
            "request_id_hint": request.get("request_id_hint"),
        }
    return {
        "tool_name": request.tool_name,
        "command": request.command,
        "description": request.description,
        "risk_attributes": request.risk_attributes,
        "request_id_hint": request.request_id_hint,
    }


def _looks_like_file_mutation(tool: str, risks: frozenset[str]) -> bool:
    tool_key = tool.casefold()
    return bool(risks & _FILE_RISKS) or tool_key in _FILE_TOOLS


def _relative_path_candidate(command: str | None) -> str | None:
    if command is None:
        return None
    candidate = command.strip().replace("\\", "/")
    if (
        not candidate
        or " " in candidate
        or "\x00" in candidate
        or candidate.startswith("/")
        or candidate.startswith("~")
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
    ):
        return None
    return candidate


__all__ = [
    "HarnessPermissionLike",
    "normalize_harness_permission_request",
    "request_harness_permission",
]
