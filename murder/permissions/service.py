"""Permission decision, review, grant, and adjacent enforcement service."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from murder.permissions.contracts import (
    PROPOSED_OPERATION_ADAPTER,
    ApprovalChoice,
    ApprovalDecisionRecord,
    ApprovalRequest,
    AuthorizationProof,
    DenyDecision,
    GrantScope,
    PermissionContext,
    PermissionDecisionRecord,
    PermissionGrant,
    PermissionPrincipal,
    ProposedOperation,
    RequireApprovalDecision,
    SafetyReviewEvidence,
    TransformDecision,
    operation_digest,
)
from murder.permissions.persistence import GrantConsumptionError, PermissionStore
from murder.permissions.policy import PermissionPolicy

_POLICY_ISSUER = PermissionPrincipal(kind="service", id="murder.permission-policy")


class PermissionDeniedError(RuntimeError):
    pass


class ApprovalRequiredError(PermissionDeniedError):
    def __init__(
        self,
        decision: PermissionDecisionRecord,
        request: ApprovalRequest,
    ) -> None:
        super().__init__(decision.reason)
        self.decision = decision
        self.request = request


class InvalidAuthorizationError(PermissionDeniedError):
    pass


class PermissionService:
    def __init__(
        self,
        *,
        store: PermissionStore,
        policy: PermissionPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        grant_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock
        self._grant_ttl = grant_ttl

    def request(self, operation: ProposedOperation) -> AuthorizationProof:
        now = self._aware_now()
        digest = operation_digest(operation)
        result = self._policy.decide(
            operation,
            PermissionContext(
                principal=operation.principal,
                operation_digest=digest,
                operation=operation,
            ),
        )
        decision = PermissionDecisionRecord(
            decision_id=uuid4(),
            operation_id=operation.operation_id,
            operation_digest=digest,
            outcome=result.outcome,
            policy_id=result.policy_id,
            decided_at=now,
        )
        approval_request = None
        authorization = None
        with self._store.atomic(immediate=True):
            self._store.save_decision(decision)
            if isinstance(result.outcome, RequireApprovalDecision):
                approval_request = ApprovalRequest(
                    approval_id=uuid4(),
                    policy_decision_id=decision.decision_id,
                    operation_digest=digest,
                    required_reviewers=result.outcome.required_reviewers,
                    reviewer_policy=result.outcome.reviewer_policy,
                    grant_scope=_scope_for(
                        operation,
                        expires_at=now + self._grant_ttl,
                    ),
                    requested_at=now,
                    requested_by=operation.principal,
                    description=result.outcome.reason,
                    payload={
                        "operation": PROPOSED_OPERATION_ADAPTER.dump_python(
                            operation,
                            mode="json",
                        ),
                    },
                )
                self._store.save_approval_request(approval_request)
            elif not isinstance(result.outcome, (DenyDecision, TransformDecision)):
                authorization = self._issue(
                    operation,
                    decision=decision,
                    approval_id=None,
                    issued_by=_POLICY_ISSUER,
                    now=now,
                )
        if isinstance(result.outcome, DenyDecision):
            raise PermissionDeniedError(result.outcome.reason)
        if isinstance(result.outcome, TransformDecision):
            raise PermissionDeniedError(
                "policy transformed the operation; submit the recorded transformed operation"
            )
        if approval_request is not None:
            raise ApprovalRequiredError(decision, approval_request)
        assert authorization is not None
        return authorization

    def decide_approval(
        self,
        operation: ProposedOperation,
        *,
        decision: PermissionDecisionRecord,
        request: ApprovalRequest,
        reviewer: PermissionPrincipal,
        choice: ApprovalChoice,
        rationale: str = "",
        prior_decisions: Sequence[ApprovalDecisionRecord] = (),
    ) -> AuthorizationProof:
        digest = operation_digest(operation)
        if decision.operation_digest != digest:
            raise InvalidAuthorizationError("approval policy decision targets another operation")
        if (
            request.policy_decision_id != decision.decision_id
            or request.operation_digest != digest
        ):
            raise InvalidAuthorizationError("approval request targets another decision")
        if prior_decisions:
            raise InvalidAuthorizationError(
                "approval evidence is loaded from persistence, not supplied by callers"
            )
        now = self._aware_now()
        from murder.state.persistence.approvals import (  # noqa: PLC0415
            resolve_standalone_approval_request,
        )

        _review, _grant, authorization = resolve_standalone_approval_request(
            self._store.connection,
            approval_id=request.approval_id,
            expected_operation_digest=digest,
            reviewer=reviewer,
            choice=choice,
            rationale=rationale,
            decided_at=now,
        )
        if authorization is None:
            persisted = self._store.get_approval_request(request.approval_id)
            status = persisted.status if persisted is not None else "unavailable"
            raise PermissionDeniedError(f"approval is {status}")
        return authorization

    # Compatibility name for the initial single-review call sites.
    approve = decide_approval

    def enforce(
        self,
        operation: ProposedOperation,
        authorization: AuthorizationProof,
    ) -> None:
        now = self._aware_now()
        digest = operation_digest(operation)
        grant = self._store.get_grant(authorization.grant_id)
        if grant is None:
            raise InvalidAuthorizationError("authorization grant does not exist")
        if grant.revoked_at is not None or self._store.grant_is_revoked(grant.grant_id):
            raise InvalidAuthorizationError("authorization grant was revoked")
        if authorization.operation_id != operation.operation_id:
            raise InvalidAuthorizationError("authorization operation id does not match")
        if authorization.operation_type != operation.type:
            raise InvalidAuthorizationError("authorization operation type does not match")
        if authorization.operation_digest != digest or grant.operation_digest != digest:
            raise InvalidAuthorizationError("authorization digest does not match")
        if authorization.subject != operation.principal or grant.issued_to != operation.principal:
            raise InvalidAuthorizationError("authorization subject does not match")
        if min(authorization.expires_at, grant.scope.expires_at) <= now:
            raise InvalidAuthorizationError("authorization has expired")
        _validate_scope(operation, grant.scope)
        operation_fence = _operation_fence(operation)
        if authorization.lease_fence != operation_fence:
            raise InvalidAuthorizationError("authorization lease fence does not match")
        try:
            recorded = self._store.record_use(authorization, enforced_at=now)
        except GrantConsumptionError as exc:
            raise InvalidAuthorizationError(str(exc)) from exc
        if not recorded:
            raise InvalidAuthorizationError("authorization proof was already used")

    def authorize_or_enforce(
        self,
        operation: ProposedOperation,
        authorization: AuthorizationProof | None,
    ) -> bool:
        try:
            proof = authorization if authorization is not None else self.request(operation)
            self.enforce(operation, proof)
        except PermissionDeniedError:
            return False
        return True

    def revoke(self, grant_id: UUID, *, reason: str) -> None:
        self._store.revoke_grant(grant_id, revoked_at=self._aware_now(), reason=reason)

    def record_safety_review(
        self,
        operation: ProposedOperation,
        evidence: SafetyReviewEvidence,
    ) -> None:
        if evidence.operation_digest != operation_digest(operation):
            raise InvalidAuthorizationError("safety review targets another operation")
        self._store.save_safety_review(evidence)

    def _issue(
        self,
        operation: ProposedOperation,
        *,
        decision: PermissionDecisionRecord,
        approval_id: UUID | None,
        issued_by: PermissionPrincipal,
        now: datetime,
        scope: GrantScope | None = None,
    ) -> AuthorizationProof:
        digest = operation_digest(operation)
        selected_scope = scope or _scope_for(
            operation,
            expires_at=now + self._grant_ttl,
        )
        grant = PermissionGrant(
            grant_id=uuid4(),
            operation_digest=digest,
            issued_to=operation.principal,
            issued_by=issued_by,
            scope=selected_scope,
            issued_at=now,
            approval_id=approval_id,
            decision_id=decision.decision_id,
        )
        self._store.save_grant(grant)
        return AuthorizationProof(
            authorization_id=uuid4(),
            grant_id=grant.grant_id,
            operation_id=operation.operation_id,
            operation_type=operation.type,
            operation_digest=digest,
            subject=operation.principal,
            issued_at=now,
            expires_at=selected_scope.expires_at,
            lease_fence=_operation_fence(operation),
        )

    def _aware_now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("permission clock must return an aware datetime")
        return now


def _scope_for(operation: ProposedOperation, *, expires_at: datetime) -> GrantScope:
    session_id = getattr(operation, "session_id", None)
    repository_id = getattr(operation, "repository_id", None)
    workflow_id = getattr(operation, "workflow_id", None)
    activity_id = getattr(operation, "activity_id", None)
    destination = getattr(operation, "destination", None)
    path = _operation_path(operation)
    return GrantScope(
        repository_ids=(repository_id,) if repository_id else (),
        workflow_ids=(workflow_id,) if workflow_id else (),
        activity_ids=(activity_id,) if activity_id else (),
        session_ids=(session_id,) if session_id else (),
        operation_types=(operation.type,),
        path_prefixes=(path,) if path else (),
        destinations=(destination,) if destination else (),
        max_uses=1,
        expires_at=expires_at,
    )


def _operation_fence(operation: ProposedOperation) -> int | None:
    fence = getattr(operation, "lease_fence", None)
    if fence is not None:
        return int(fence)
    takeover_fence = getattr(operation, "current_lease_fence", None)
    return None if takeover_fence is None else int(takeover_fence)


def _operation_path(operation: ProposedOperation) -> str | None:
    path = getattr(operation, "path", None)
    if path is not None:
        return str(path)
    worktree_path = getattr(operation, "worktree_path", None)
    return None if worktree_path is None else str(worktree_path)


def _validate_scope(operation: ProposedOperation, scope: GrantScope) -> None:
    if operation.type not in scope.operation_types:
        raise InvalidAuthorizationError("grant does not include this operation type")
    scoped_dimensions = (
        ("repository", getattr(operation, "repository_id", None), scope.repository_ids),
        ("workflow", getattr(operation, "workflow_id", None), scope.workflow_ids),
        ("activity", getattr(operation, "activity_id", None), scope.activity_ids),
        ("session", getattr(operation, "session_id", None), scope.session_ids),
    )
    for name, identifier, allowed in scoped_dimensions:
        if identifier is not None and identifier not in allowed:
            raise InvalidAuthorizationError(f"grant does not include this {name}")
    path = _operation_path(operation)
    if path is not None and not any(
        path == prefix or path.startswith(f"{prefix.rstrip('/')}/")
        for prefix in scope.path_prefixes
    ):
        raise InvalidAuthorizationError("grant does not include this path")
    destination = getattr(operation, "destination", None)
    if destination is not None and destination not in scope.destinations:
        raise InvalidAuthorizationError("grant does not include this destination")


__all__ = [
    "ApprovalRequiredError",
    "InvalidAuthorizationError",
    "PermissionDeniedError",
    "PermissionService",
]
