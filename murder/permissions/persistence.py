"""Feature-owned persistence for permission decisions and evidence."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel

from murder.facts.contracts import (
    AggregateRef,
    FactActor,
    FactCorrelation,
    ProjectionInputDraft,
    RetainedFactDraft,
)
from murder.facts.log import append_fact, ensure_fact_schema
from murder.permissions.contracts import (
    ApprovalEvidence,
    ApprovalRequest,
    AuthorizationGrant,
    OperationAuthorization,
    PolicyDecision,
    SafetyReviewEvidence,
)


class GrantConsumptionError(RuntimeError):
    pass

PERMISSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permission_policy_decisions (
    decision_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    operation_digest TEXT NOT NULL,
    outcome TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_permission_decision_digest
ON permission_policy_decisions(operation_digest);

CREATE TABLE IF NOT EXISTS permission_approval_evidence (
    decision_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    operation_digest TEXT NOT NULL,
    choice TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_approval_requests (
    approval_id TEXT PRIMARY KEY,
    policy_decision_id TEXT NOT NULL,
    operation_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    workflow_id TEXT,
    workflow_revision INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_authorization_grants (
    grant_id TEXT PRIMARY KEY,
    operation_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_authorization_uses (
    authorization_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    operation_digest TEXT NOT NULL,
    enforced_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_grant_revocations (
    grant_id TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permission_safety_reviews (
    review_id TEXT PRIMARY KEY,
    operation_digest TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class PermissionStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        ensure_permission_schema(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def save_decision(self, decision: PolicyDecision) -> None:
        self._connection.execute(
            """
            INSERT INTO permission_policy_decisions
            (decision_id, operation_id, operation_digest, outcome, policy_id,
             reason, decided_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(decision.decision_id),
                str(decision.operation_id),
                decision.operation_digest,
                decision.outcome.type,
                decision.policy_id,
                decision.reason,
                decision.decided_at.isoformat(),
                _payload(decision),
            ),
        )

    def save_approval(self, approval: ApprovalEvidence) -> None:
        self._connection.execute(
            """
            INSERT INTO permission_approval_evidence
            (decision_id, approval_id, operation_digest, choice, decided_at,
             payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(approval.decision_id),
                str(approval.approval_id),
                approval.operation_digest,
                approval.choice.value,
                approval.decided_at.isoformat(),
                _payload(approval),
            ),
        )

    def save_approval_request(self, request: ApprovalRequest) -> None:
        self._connection.execute(
            """
            INSERT INTO permission_approval_requests
            (approval_id, policy_decision_id, operation_digest, status, requested_at,
             workflow_id, workflow_revision, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(request.approval_id),
                str(request.policy_decision_id),
                request.operation_digest,
                request.status,
                request.requested_at.isoformat(),
                str(request.workflow_id) if request.workflow_id else None,
                request.workflow_revision,
                _payload(request),
            ),
        )
        append_fact(
            self._connection,
            RetainedFactDraft(
                fact_id=uuid5(
                    NAMESPACE_URL,
                    f"murder:approval-requested:{request.approval_id}",
                ),
                kind="permission.approval.requested",
                occurred_at=request.requested_at,
                aggregate=AggregateRef(
                    kind="workflow" if request.workflow_id is not None else "permission",
                    id=request.workflow_id or request.policy_decision_id,
                    revision=request.workflow_revision,
                ),
                actor=FactActor(
                    kind=request.requested_by.kind,
                    id=request.requested_by.id,
                ),
                correlation=FactCorrelation(
                    correlation_id=request.policy_decision_id
                ),
                payload={
                    "approval_id": str(request.approval_id),
                    "operation_digest": request.operation_digest,
                    "status": request.status,
                    "workflow_id": (
                        str(request.workflow_id)
                        if request.workflow_id is not None
                        else None
                    ),
                },
            ),
            projection_inputs=(
                ProjectionInputDraft(
                    projection="approvals",
                    subject_key=str(request.approval_id),
                    generation=request.workflow_revision or 0,
                ),
            ),
            recorded_at=request.requested_at,
        )

    def get_approval_request(self, approval_id: UUID) -> ApprovalRequest | None:
        row = self._connection.execute(
            """
            SELECT payload_json FROM permission_approval_requests
            WHERE approval_id = ?
            """,
            (str(approval_id),),
        ).fetchone()
        return None if row is None else ApprovalRequest.model_validate_json(row[0])

    def get_decision_by_operation_id(self, operation_id: UUID) -> PolicyDecision | None:
        row = self._connection.execute(
            """
            SELECT payload_json FROM permission_policy_decisions
            WHERE operation_id = ?
            ORDER BY decided_at DESC, decision_id DESC
            LIMIT 1
            """,
            (str(operation_id),),
        ).fetchone()
        return None if row is None else PolicyDecision.model_validate_json(row[0])

    def get_pending_approval_for_operation(
        self,
        operation_id: UUID,
    ) -> tuple[PolicyDecision, ApprovalRequest] | None:
        decision = self.get_decision_by_operation_id(operation_id)
        if decision is None:
            return None
        row = self._connection.execute(
            """
            SELECT payload_json FROM permission_approval_requests
            WHERE policy_decision_id = ? AND status = 'pending'
            ORDER BY requested_at DESC, approval_id DESC
            LIMIT 1
            """,
            (str(decision.decision_id),),
        ).fetchone()
        if row is None:
            return None
        return decision, ApprovalRequest.model_validate_json(row[0])

    def list_approval_requests(
        self,
        *,
        status: str | None = None,
        workflow_id: UUID | None = None,
    ) -> tuple[ApprovalRequest, ...]:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(str(workflow_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT payload_json FROM permission_approval_requests
            {where}
            ORDER BY requested_at, approval_id
            """,
            tuple(params),
        ).fetchall()
        return tuple(ApprovalRequest.model_validate_json(row[0]) for row in rows)

    def save_grant(self, grant: AuthorizationGrant) -> None:
        self._connection.execute(
            """
            INSERT INTO permission_authorization_grants
            (grant_id, operation_digest, scope_json, issued_at, expires_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(grant.grant_id),
                grant.operation_digest,
                json.dumps(grant.scope.model_dump(mode="json"), sort_keys=True),
                grant.issued_at.isoformat(),
                grant.scope.expires_at.isoformat(),
                _payload(grant),
            ),
        )
        append_fact(
            self._connection,
            RetainedFactDraft(
                fact_id=grant.grant_id,
                kind="permission.grant.issued",
                occurred_at=grant.issued_at,
                aggregate=AggregateRef(kind="permission_grant", id=grant.grant_id),
                actor=FactActor(kind=grant.issued_by.kind, id=grant.issued_by.id),
                correlation=FactCorrelation(correlation_id=grant.decision_id),
                payload={
                    "grant_id": str(grant.grant_id),
                    "approval_id": (
                        str(grant.approval_id) if grant.approval_id is not None else None
                    ),
                    "operation_digest": grant.operation_digest,
                    "issued_to": grant.issued_to.model_dump(mode="json"),
                    "scope": grant.scope.model_dump(mode="json"),
                },
            ),
            projection_inputs=(
                ProjectionInputDraft(
                    projection="permissions",
                    subject_key=str(grant.grant_id),
                    generation=0,
                ),
            ),
            recorded_at=grant.issued_at,
        )

    def get_grant(self, grant_id: UUID) -> AuthorizationGrant | None:
        row = self._connection.execute(
            "SELECT payload_json FROM permission_authorization_grants WHERE grant_id = ?",
            (str(grant_id),),
        ).fetchone()
        if row is None:
            return None
        grant = AuthorizationGrant.model_validate_json(row[0])
        revocation = self._connection.execute(
            """
            SELECT revoked_at, reason FROM permission_grant_revocations
            WHERE grant_id = ?
            """,
            (str(grant_id),),
        ).fetchone()
        updates: dict[str, object] = {"uses": self.grant_use_count(grant_id)}
        if revocation is not None:
            updates.update(
                revoked_at=datetime.fromisoformat(str(revocation[0])),
                revocation_reason=str(revocation[1]),
            )
        return grant.model_copy(update=updates)

    def list_grants(self) -> tuple[AuthorizationGrant, ...]:
        rows = self._connection.execute(
            """
            SELECT grant_id FROM permission_authorization_grants
            ORDER BY issued_at, grant_id
            """
        ).fetchall()
        grants = tuple(self.get_grant(UUID(str(row[0]))) for row in rows)
        return tuple(grant for grant in grants if grant is not None)

    def revoke_grant(self, grant_id: UUID, *, revoked_at: datetime, reason: str) -> None:
        with self._atomic(immediate=True):
            grant = self.get_grant(grant_id)
            if grant is None:
                raise ValueError(f"permission grant {grant_id} does not exist")
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO permission_grant_revocations
                (grant_id, revoked_at, reason) VALUES (?, ?, ?)
                """,
                (str(grant_id), revoked_at.isoformat(), reason),
            )
            if cursor.rowcount != 1:
                return
            fact_id = uuid5(
                NAMESPACE_URL,
                f"murder:permission-grant-revoked:{grant_id}",
            )
            append_fact(
                self._connection,
                RetainedFactDraft(
                    fact_id=fact_id,
                    kind="permission.grant.revoked",
                    occurred_at=revoked_at,
                    aggregate=AggregateRef(kind="permission_grant", id=grant_id),
                    actor=FactActor(kind="service", id="murder.permission-policy"),
                    correlation=FactCorrelation(correlation_id=grant.decision_id),
                    payload={
                        "grant_id": str(grant_id),
                        "reason": reason,
                        "uses": grant.uses,
                    },
                ),
                projection_inputs=(
                    ProjectionInputDraft(
                        projection="permissions",
                        subject_key=str(grant_id),
                        generation=grant.uses + 1,
                    ),
                ),
                recorded_at=revoked_at,
            )

    def grant_is_revoked(self, grant_id: UUID) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM permission_grant_revocations WHERE grant_id = ?",
            (str(grant_id),),
        ).fetchone()
        return row is not None

    def grant_use_count(self, grant_id: UUID) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM permission_authorization_uses WHERE grant_id = ?",
            (str(grant_id),),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def save_safety_review(self, evidence: SafetyReviewEvidence) -> None:
        self._connection.execute(
            """
            INSERT INTO permission_safety_reviews
            (review_id, operation_digest, verdict, reviewed_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(evidence.review_id),
                evidence.operation_digest,
                evidence.verdict,
                evidence.reviewed_at.isoformat(),
                _payload(evidence),
            ),
        )

    def record_use(
        self,
        authorization: OperationAuthorization,
        *,
        enforced_at: datetime,
    ) -> bool:
        with self._atomic(immediate=True):
            grant = self.get_grant(authorization.grant_id)
            if grant is None:
                raise GrantConsumptionError("authorization grant does not exist")
            if grant.revoked_at is not None:
                raise GrantConsumptionError("authorization grant was revoked")
            if grant.uses >= grant.scope.max_uses:
                raise GrantConsumptionError(
                    "authorization grant use limit was reached"
                )
            try:
                self._connection.execute(
                    """
                    INSERT INTO permission_authorization_uses
                    (authorization_id, grant_id, operation_id, operation_digest,
                     enforced_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(authorization.authorization_id),
                        str(authorization.grant_id),
                        str(authorization.operation_id),
                        authorization.operation_digest,
                        enforced_at.isoformat(),
                        _payload(authorization),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            use_number = grant.uses + 1
            append_fact(
                self._connection,
                RetainedFactDraft(
                    fact_id=authorization.authorization_id,
                    kind="permission.grant.used",
                    occurred_at=enforced_at,
                    aggregate=AggregateRef(
                        kind="permission_grant",
                        id=authorization.grant_id,
                        revision=use_number,
                    ),
                    actor=FactActor(
                        kind=authorization.subject.kind,
                        id=authorization.subject.id,
                    ),
                    correlation=FactCorrelation(
                        correlation_id=authorization.authorization_id
                    ),
                    payload={
                        "grant_id": str(authorization.grant_id),
                        "authorization_id": str(authorization.authorization_id),
                        "operation_id": str(authorization.operation_id),
                        "operation_digest": authorization.operation_digest,
                        "use_number": use_number,
                    },
                ),
                projection_inputs=(
                    ProjectionInputDraft(
                        projection="permissions",
                        subject_key=str(authorization.grant_id),
                        generation=use_number,
                    ),
                ),
                recorded_at=enforced_at,
            )
            return True

    def count(self, table: str) -> int:
        allowed = {
            "permission_policy_decisions",
            "permission_approval_evidence",
            "permission_approval_requests",
            "permission_authorization_grants",
            "permission_authorization_uses",
            "permission_grant_revocations",
            "permission_safety_reviews",
        }
        if table not in allowed:
            raise ValueError("unknown permission table")
        row = self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])

    def atomic(self, *, immediate: bool = True) -> AbstractContextManager[None]:
        return self._atomic(immediate=immediate)

    @contextmanager
    def _atomic(self, *, immediate: bool) -> Iterator[None]:
        if self._connection.in_transaction:
            name = f"permission_{uuid4().hex}"
            self._connection.execute(f"SAVEPOINT {name}")
            try:
                yield
            except BaseException:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
                self._connection.execute(f"RELEASE SAVEPOINT {name}")
                raise
            else:
                self._connection.execute(f"RELEASE SAVEPOINT {name}")
            return
        self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()


def _payload(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def ensure_permission_schema(connection: sqlite3.Connection) -> None:
    """Create feature tables without committing an enclosing transaction."""

    ensure_fact_schema(connection)
    for statement in PERMISSION_SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


__all__ = [
    "GrantConsumptionError",
    "PERMISSION_SCHEMA_SQL",
    "PermissionStore",
    "ensure_permission_schema",
]
