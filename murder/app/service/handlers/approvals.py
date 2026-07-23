"""Typed human-facing approval query and decision handlers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Protocol

from murder.app.protocol.permissions import (
    DecideApprovalParams,
    GetApprovalParams,
    ListApprovalsParams,
    ListPermissionsParams,
)
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.permissions.persistence import PermissionStore
from murder.state.persistence.approvals import (
    resolve_approval_request,
    resolve_standalone_approval_request,
)

class ApprovalEffects(Protocol):
    """Runtime capabilities required by approval and grant use cases."""

    db: sqlite3.Connection | None


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    effects: ApprovalEffects,
) -> None:
    """Register approval use cases and authoritative approval projections."""
    def _db() -> sqlite3.Connection:
        connection = effects.db
        if connection is None:
            raise RuntimeError("service not started")
        return connection

    def _list(body: dict[str, Any]) -> dict[str, Any]:
        params = ListApprovalsParams.model_validate(body)
        requests = PermissionStore(_db()).list_approval_requests(
            status=params.status,
            workflow_id=params.workflow_id,
        )
        return {
            "approvals": [item.model_dump(mode="json") for item in requests],
        }

    def _get(body: dict[str, Any]) -> dict[str, Any]:
        params = GetApprovalParams.model_validate(body)
        request = PermissionStore(_db()).get_approval_request(params.approval_id)
        if request is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "approval": request.model_dump(mode="json")}

    def _list_permissions(body: dict[str, Any]) -> dict[str, Any]:
        ListPermissionsParams.model_validate(body)
        grants = PermissionStore(_db()).list_grants()
        return {
            "grants": [item.model_dump(mode="json") for item in grants],
        }

    def _decide(body: dict[str, Any]) -> dict[str, Any]:
        params = DecideApprovalParams.model_validate(body)
        if params.reviewer is None:
            raise ValueError("approval.decide requires a reviewer")
        reviewer = params.reviewer
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
                reviewer=reviewer,
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
                reviewer=reviewer,
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

    app.register_application_query(QueryName.APPROVALS_LIST, _list)
    app.register_application_query(QueryName.APPROVALS_GET, _get)
    app.register_application_query(QueryName.PERMISSIONS_LIST, _list_permissions)
    app.register_application_command(CommandName.APPROVAL_DECIDE, _decide)
    projections.register(ProjectionTopic.APPROVALS, lambda: _list({}))
    projections.register(ProjectionTopic.PERMISSIONS, lambda: _list_permissions({}))


__all__ = ["ApprovalEffects", "register"]
