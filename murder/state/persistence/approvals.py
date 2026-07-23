"""Atomic workflow integration for the authoritative permission approval store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from murder.facts.contracts import (
    AggregateRef,
    FactCorrelation,
    PrivateFactPayload,
    ProjectionInputDraft,
    RetainedFactDraft,
    fact_actor,
)
from murder.facts.log import append_fact
from murder.permissions.contracts import (
    PROPOSED_OPERATION_ADAPTER,
    ApprovalChoice,
    ApprovalDecisionRecord,
    ApprovalRequest,
    AuthorizationProof,
    PermissionDecisionRecord,
    PermissionGrant,
    PermissionPrincipal,
    RequireApprovalDecision,
    operation_digest,
)
from murder.permissions.persistence import PermissionStore, ensure_permission_schema
from murder.work.workflows.runtime import ApprovalRequestDraft, ApprovalResolvedSignal

_SHA256_HEX_LENGTH = 64


def insert_approval_requests(
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    workflow_revision: int,
    drafts: Sequence[ApprovalRequestDraft],
    created_at: datetime,
) -> tuple[ApprovalRequest, ...]:
    """Persist policy decisions and approval requests in the transition transaction."""

    ensure_permission_schema(conn)
    store = PermissionStore(conn)
    records = []
    for draft in drafts:
        if len(draft.operation_digest) != _SHA256_HEX_LENGTH:
            raise ValueError("workflow approval operation_digest must be a SHA-256 digest")
        policy_decision_id = uuid5(
            NAMESPACE_URL,
            f"murder:approval-policy:{workflow_id}:{workflow_revision}:{draft.approval_id}",
        )
        policy_outcome = RequireApprovalDecision(
            reason=draft.summary,
            required_reviewers=draft.required_reviewers,
            reviewer_policy=draft.policy,
        )
        store.save_decision(
            PermissionDecisionRecord(
                decision_id=policy_decision_id,
                operation_id=draft.approval_id,
                operation_digest=draft.operation_digest,
                outcome=policy_outcome,
                policy_id="workflow.transition.v1",
                decided_at=created_at,
            )
        )
        request = ApprovalRequest(
            approval_id=draft.approval_id,
            policy_decision_id=policy_decision_id,
            operation_digest=draft.operation_digest,
            required_reviewers=draft.required_reviewers,
            reviewer_policy=draft.policy,
            grant_scope=draft.grant_scope,
            requested_at=created_at,
            requested_by=draft.requested_by,
            workflow_id=workflow_id,
            workflow_revision=workflow_revision,
            description=draft.summary,
            payload=draft.model_dump(mode="json"),
        )
        store.save_approval_request(request)
        records.append(request)
    return tuple(records)


def resolve_approval_request(  # noqa: PLR0912 - transactional invariants
    conn: sqlite3.Connection,
    *,
    workflow_id: UUID,
    approval_id: UUID,
    expected_workflow_revision: int,
    expected_operation_digest: str,
    reviewer: PermissionPrincipal,
    choice: ApprovalChoice,
    rationale: str,
    decided_at: datetime,
) -> tuple[
    ApprovalDecisionRecord,
    PermissionGrant | None,
    AuthorizationProof | None,
]:
    """Resolve policy and atomically grant, signal, and append an audit fact."""

    from murder.state.persistence.workflow_runs import (  # noqa: PLC0415
        enqueue_workflow_signal,
    )

    ensure_permission_schema(conn)
    with _transaction(conn, immediate=True):
        row = conn.execute(
            """
            SELECT payload_json FROM permission_approval_requests
            WHERE approval_id = ? AND workflow_id = ?
            """,
            (str(approval_id), str(workflow_id)),
        ).fetchone()
        if row is None:
            raise ValueError(f"approval {approval_id} does not exist for workflow {workflow_id}")
        request = ApprovalRequest.model_validate_json(row[0])
        if request.status != "pending":
            raise ValueError(f"approval {approval_id} is already {request.status}")
        if request.workflow_revision != expected_workflow_revision:
            raise ValueError("approval workflow revision does not match")
        if request.operation_digest != expected_operation_digest:
            raise ValueError("approval operation digest does not match")
        run = conn.execute(
            """
            SELECT status, revision FROM workflow_runs
            WHERE workflow_id = ?
            """,
            (str(workflow_id),),
        ).fetchone()
        if run is None:
            raise ValueError(f"workflow {workflow_id} does not exist")
        if int(run["revision"]) != expected_workflow_revision:
            raise ValueError("current workflow revision does not match approval")
        if str(run["status"]) != "waiting":
            raise ValueError("workflow is not waiting for approval")
        if not _has_active_approval_wait(conn, workflow_id, approval_id):
            raise ValueError("workflow has no active wait for approval")
        if (
            request.grant_scope.expires_at is not None
            and request.grant_scope.expires_at <= decided_at
        ):
            raise ValueError("approval request has expired")
        reviewer_kind = _reviewer_kind(reviewer)
        if reviewer_kind not in request.required_reviewers:
            raise ValueError(
                f"reviewer kind {reviewer_kind!r} is not eligible for approval"
            )
        raw_operation = request.payload.get("operation")
        operation = None
        if isinstance(raw_operation, dict):
            operation = PROPOSED_OPERATION_ADAPTER.validate_python(raw_operation)
            if operation_digest(operation) != expected_operation_digest:
                raise ValueError("persisted proposed operation digest does not match")

        decision = ApprovalDecisionRecord(
            decision_id=uuid4(),
            approval_id=approval_id,
            operation_digest=expected_operation_digest,
            choice=choice,
            reviewer=reviewer,
            rationale=rationale,
            decided_at=decided_at,
        )
        store = PermissionStore(conn)
        store.save_approval(decision)
        decisions = _approval_decisions(conn, approval_id)
        status = _resolve_status(request, decisions)
        grant = None
        authorization = None
        if status == "approved":
            grant = PermissionGrant(
                grant_id=uuid4(),
                operation_digest=request.operation_digest,
                issued_to=request.requested_by,
                issued_by=reviewer,
                scope=request.grant_scope,
                issued_at=decided_at,
                approval_id=request.approval_id,
                decision_id=request.policy_decision_id,
            )
            store.save_grant(grant)
            if operation is not None:
                operation_fence = getattr(
                    operation,
                    "lease_fence",
                    getattr(operation, "current_lease_fence", None),
                )
                authorization = AuthorizationProof(
                    authorization_id=uuid4(),
                    grant_id=grant.grant_id,
                    operation_id=operation.operation_id,
                    operation_type=operation.type,
                    operation_digest=request.operation_digest,
                    subject=operation.principal,
                    issued_at=decided_at,
                    expires_at=request.grant_scope.expires_at,
                    lease_fence=operation_fence,
                )
            else:
                operation_types = request.grant_scope.operation_types
                if not operation_types:
                    raise ValueError(
                        "approval request has no operation type for authorization"
                    )
                authorization = AuthorizationProof(
                    authorization_id=uuid4(),
                    grant_id=grant.grant_id,
                    operation_id=approval_id,
                    operation_type=operation_types[0],
                    operation_digest=request.operation_digest,
                    subject=request.requested_by,
                    issued_at=decided_at,
                    expires_at=request.grant_scope.expires_at,
                    lease_fence=None,
                )

        updated = request.model_copy(update={"status": status})
        conn.execute(
            """
            UPDATE permission_approval_requests
            SET status = ?, payload_json = ?
            WHERE approval_id = ? AND status = 'pending'
            """,
            (
                status,
                json.dumps(
                    updated.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(approval_id),
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError("approval resolution lost a concurrent update")
        if status in {"approved", "denied"}:
            enqueue_workflow_signal(
                conn,
                workflow_id=workflow_id,
                deduplication_key=f"approval:{approval_id}",
                payload=ApprovalResolvedSignal(
                    approval_id=approval_id,
                    decision_id=decision.decision_id,
                ),
                created_at=decided_at,
            )
            append_fact(
                conn,
                RetainedFactDraft(
                    fact_id=uuid5(
                        NAMESPACE_URL,
                        f"murder:approval-resolved:{approval_id}:{decision.decision_id}",
                    ),
                    occurred_at=decided_at,
                    aggregate=AggregateRef(
                        kind="workflow",
                        id=workflow_id,
                        revision=expected_workflow_revision,
                    ),
                    actor=fact_actor(reviewer),
                    correlation=FactCorrelation(correlation_id=decision.decision_id),
                    payload=PrivateFactPayload(
                        kind="permission.approval.resolved",
                        data={
                            "approval_id": str(approval_id),
                            "decision_id": str(decision.decision_id),
                            "operation_digest": expected_operation_digest,
                            "status": status,
                            "grant_id": str(grant.grant_id) if grant else None,
                        },
                    ),
                ),
                projection_inputs=(
                    ProjectionInputDraft(
                        projection="approvals",
                        subject_key=str(approval_id),
                        generation=expected_workflow_revision,
                    ),
                ),
                recorded_at=decided_at,
            )
    return decision, grant, authorization


def resolve_standalone_approval_request(
    conn: sqlite3.Connection,
    *,
    approval_id: UUID,
    expected_operation_digest: str,
    reviewer: PermissionPrincipal,
    choice: ApprovalChoice,
    rationale: str,
    decided_at: datetime,
) -> tuple[
    ApprovalDecisionRecord,
    PermissionGrant | None,
    AuthorizationProof | None,
]:
    """Resolve a non-workflow approval from persisted evidence only."""

    ensure_permission_schema(conn)
    with _transaction(conn, immediate=True):
        row = conn.execute(
            """
            SELECT payload_json FROM permission_approval_requests
            WHERE approval_id = ? AND workflow_id IS NULL
            """,
            (str(approval_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"standalone approval {approval_id} does not exist")
        request = ApprovalRequest.model_validate_json(row[0])
        if request.status != "pending":
            raise ValueError(f"approval {approval_id} is already {request.status}")
        if request.operation_digest != expected_operation_digest:
            raise ValueError("approval operation digest does not match")
        if request.grant_scope.expires_at <= decided_at:
            raise ValueError("approval request has expired")
        reviewer_kind = _reviewer_kind(reviewer)
        if reviewer_kind not in request.required_reviewers:
            raise ValueError(
                f"reviewer kind {reviewer_kind!r} is not eligible for approval"
            )
        raw_operation = request.payload.get("operation")
        if not isinstance(raw_operation, dict):
            raise ValueError("approval request does not contain its proposed operation")
        operation = PROPOSED_OPERATION_ADAPTER.validate_python(raw_operation)
        if operation_digest(operation) != expected_operation_digest:
            raise ValueError("persisted proposed operation digest does not match")

        decision = ApprovalDecisionRecord(
            decision_id=uuid4(),
            approval_id=approval_id,
            operation_digest=expected_operation_digest,
            choice=choice,
            reviewer=reviewer,
            rationale=rationale,
            decided_at=decided_at,
        )
        store = PermissionStore(conn)
        store.save_approval(decision)
        status = _resolve_status(request, _approval_decisions(conn, approval_id))
        grant = None
        authorization = None
        if status == "approved":
            grant = PermissionGrant(
                grant_id=uuid4(),
                operation_digest=request.operation_digest,
                issued_to=request.requested_by,
                issued_by=reviewer,
                scope=request.grant_scope,
                issued_at=decided_at,
                approval_id=request.approval_id,
                decision_id=request.policy_decision_id,
            )
            store.save_grant(grant)
            operation_fence = getattr(
                operation,
                "lease_fence",
                getattr(operation, "current_lease_fence", None),
            )
            authorization = AuthorizationProof(
                authorization_id=uuid4(),
                grant_id=grant.grant_id,
                operation_id=operation.operation_id,
                operation_type=operation.type,
                operation_digest=request.operation_digest,
                subject=operation.principal,
                issued_at=decided_at,
                expires_at=request.grant_scope.expires_at,
                lease_fence=operation_fence,
            )

        updated = request.model_copy(update={"status": status})
        conn.execute(
            """
            UPDATE permission_approval_requests
            SET status = ?, payload_json = ?
            WHERE approval_id = ? AND status = 'pending'
            """,
            (
                status,
                json.dumps(
                    updated.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                str(approval_id),
            ),
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError("approval resolution lost a concurrent update")
        if status in {"approved", "denied"}:
            append_fact(
                conn,
                RetainedFactDraft(
                    fact_id=uuid5(
                        NAMESPACE_URL,
                        f"murder:approval-resolved:{approval_id}:{decision.decision_id}",
                    ),
                    occurred_at=decided_at,
                    aggregate=AggregateRef(
                        kind="permission",
                        id=request.policy_decision_id,
                    ),
                    actor=fact_actor(reviewer),
                    correlation=FactCorrelation(
                        correlation_id=decision.decision_id
                    ),
                    payload=PrivateFactPayload(
                        kind="permission.approval.resolved",
                        data={
                            "approval_id": str(approval_id),
                            "decision_id": str(decision.decision_id),
                            "operation_digest": expected_operation_digest,
                            "status": status,
                            "grant_id": str(grant.grant_id) if grant else None,
                        },
                    ),
                ),
                projection_inputs=(
                    ProjectionInputDraft(
                        projection="approvals",
                        subject_key=str(approval_id),
                        generation=1,
                    ),
                ),
                recorded_at=decided_at,
            )
    return decision, grant, authorization


def _approval_decisions(
    conn: sqlite3.Connection,
    approval_id: UUID,
) -> tuple[ApprovalDecisionRecord, ...]:
    rows = conn.execute(
        """
        SELECT payload_json FROM permission_approval_evidence
        WHERE approval_id = ? ORDER BY decided_at, decision_id
        """,
        (str(approval_id),),
    ).fetchall()
    return tuple(ApprovalDecisionRecord.model_validate_json(row[0]) for row in rows)


def _resolve_status(
    request: ApprovalRequest,
    decisions: Sequence[ApprovalDecisionRecord],
) -> str:
    if any(item.choice is ApprovalChoice.DENY for item in decisions):
        return "denied"
    approved = [item for item in decisions if item.choice is ApprovalChoice.APPROVE]
    kinds = {_reviewer_kind(item.reviewer) for item in approved}
    if request.reviewer_policy == "any":
        return "approved" if approved else "pending"
    if request.reviewer_policy == "human_required":
        return "approved" if "human" in kinds else "pending"
    return "approved" if set(request.required_reviewers).issubset(kinds) else "pending"


def _reviewer_kind(reviewer: PermissionPrincipal) -> str:
    if reviewer.kind == "llm":
        return "llm"
    if reviewer.kind in {"user", "client", "reviewer"}:
        return "human"
    raise ValueError(f"principal kind {reviewer.kind!r} cannot review approvals")


def _has_active_approval_wait(
    conn: sqlite3.Connection,
    workflow_id: UUID,
    approval_id: UUID,
) -> bool:
    rows = conn.execute(
        """
        SELECT spec_json FROM workflow_waits
        WHERE workflow_id = ? AND satisfied_at IS NULL
        """,
        (str(workflow_id),),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["spec_json"])
        if (
            payload.get("type") == "approval"
            and payload.get("approval_id") == str(approval_id)
        ):
            return True
    return False


@contextmanager
def _transaction(
    conn: sqlite3.Connection,
    *,
    immediate: bool,
) -> Iterator[None]:
    if conn.in_transaction:
        savepoint = f"resolve_approval_{uuid4().hex}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


__all__ = [
    "insert_approval_requests",
    "resolve_approval_request",
    "resolve_standalone_approval_request",
]
