"""Typed human-facing approval query and decision handlers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from murder.permissions.contracts import ApprovalChoice, PermissionPrincipal
from murder.permissions.persistence import PermissionStore
from murder.state.persistence.approvals import (
    resolve_approval_request,
    resolve_standalone_approval_request,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListApprovalsParams(_Params):
    status: Literal[
        "pending",
        "approved",
        "denied",
        "expired",
        "cancelled",
    ] | None = None
    workflow_id: UUID | None = None


class GetApprovalParams(_Params):
    approval_id: UUID


class DecideApprovalParams(_Params):
    approval_id: UUID
    workflow_id: UUID | None = None
    expected_workflow_revision: int | None = Field(default=None, ge=0)
    expected_operation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    choice: ApprovalChoice
    rationale: str = Field(min_length=1, max_length=4000)
    reviewer: PermissionPrincipal


def register(host: ServiceHost) -> None:
    def _db() -> sqlite3.Connection:
        runtime = host.runtime
        if runtime is None or runtime.db is None:
            raise RuntimeError("service not started")
        return runtime.db

    def _list(body: dict[str, object]) -> dict[str, object]:
        params = ListApprovalsParams.model_validate(body)
        requests = PermissionStore(_db()).list_approval_requests(
            status=params.status,
            workflow_id=params.workflow_id,
        )
        return {
            "approvals": [item.model_dump(mode="json") for item in requests],
        }

    def _get(body: dict[str, object]) -> dict[str, object]:
        params = GetApprovalParams.model_validate(body)
        request = PermissionStore(_db()).get_approval_request(params.approval_id)
        if request is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "approval": request.model_dump(mode="json")}

    def _list_permissions(body: dict[str, object]) -> dict[str, object]:
        _Params.model_validate(body)
        grants = PermissionStore(_db()).list_grants()
        return {
            "grants": [item.model_dump(mode="json") for item in grants],
        }

    def _decide(body: dict[str, object]) -> dict[str, object]:
        params = DecideApprovalParams.model_validate(body)
        connection = _db()
        request = PermissionStore(connection).get_approval_request(params.approval_id)
        if request is None:
            raise ValueError(f"approval {params.approval_id} does not exist")
        now = datetime.now(timezone.utc)
        if request.workflow_id is not None:
            if (
                params.workflow_id != request.workflow_id
                or params.expected_workflow_revision is None
            ):
                raise ValueError(
                    "workflow approval requires its workflow id and expected revision"
                )
            decision, grant, authorization = resolve_approval_request(
                connection,
                workflow_id=params.workflow_id,
                approval_id=params.approval_id,
                expected_workflow_revision=params.expected_workflow_revision,
                expected_operation_digest=params.expected_operation_digest,
                reviewer=params.reviewer,
                choice=params.choice,
                rationale=params.rationale,
                decided_at=now,
            )
        else:
            if (
                params.workflow_id is not None
                or params.expected_workflow_revision is not None
            ):
                raise ValueError("standalone approval does not belong to a workflow")
            decision, grant, authorization = resolve_standalone_approval_request(
                connection,
                approval_id=params.approval_id,
                expected_operation_digest=params.expected_operation_digest,
                reviewer=params.reviewer,
                choice=params.choice,
                rationale=params.rationale,
                decided_at=now,
            )
        return {
            "decision": decision.model_dump(mode="json"),
            "grant": grant.model_dump(mode="json") if grant is not None else None,
            "authorization": (
                authorization.model_dump(mode="json")
                if authorization is not None
                else None
            ),
        }

    host.register_rpc_handler("approvals.list", _list)
    host.register_rpc_handler("approvals.get", _get)
    host.register_rpc_handler("approval.decide", _decide)
    host.register_rpc_handler("permissions.list", _list_permissions)


__all__ = [
    "DecideApprovalParams",
    "GetApprovalParams",
    "ListApprovalsParams",
    "register",
]
