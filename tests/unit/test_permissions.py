from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from murder.app.service.handlers import approvals as approval_handlers
from murder.facts.log import replay_facts, replay_projection_inputs
from murder.permissions import (
    ApprovalChoice,
    ApprovalRequiredError,
    AuthorizationProof,
    InvalidAuthorizationError,
    LocalServicePermissionPolicy,
    PermissionDeniedError,
    PermissionPrincipal,
    PermissionService,
    PermissionStore,
    SideEffectEnforcer,
    TerminalWrite,
    ToolInvocation,
    normalize_harness_permission_request,
    operation_digest,
    request_harness_permission,
)
from murder.permissions.contracts import FileMutation
from murder.permissions.session import (
    SessionPermissionAuthorizer,
    normalize_session_command,
    normalize_writer_takeover,
)
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    LeaseResource,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RequestMeta,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    WriterLease,
    WriterLeaseDenied,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.controller import SessionAuthorizationError, SessionController
from murder.runtime.sessions.persistence import SessionStore, ensure_session_schema

NOW = datetime(2026, 7, 18, 12, tzinfo=timezone.utc)


class RecordingBackend:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    async def recover(self, record: HarnessSessionRecord) -> None:
        del record

    async def send_structured_message(self, command: object) -> None:
        del command

    async def write_terminal_input(self, command: object, data: bytes) -> None:
        del command
        self.writes.append(data)

    async def resize_terminal(self, command: object) -> None:
        del command

    async def interrupt(self, command: object) -> None:
        del command

    async def terminate(self, command: object) -> None:
        del command


def principal(
    kind: PrincipalKind = PrincipalKind.CLIENT,
    identifier: str = "client",
) -> PrincipalRef:
    return PrincipalRef(kind=kind, id=identifier)


def operation(*, text: str = "hello", fence: int = 4) -> TerminalWrite:
    return TerminalWrite(
        operation_id=uuid4(),
        principal=PermissionPrincipal(kind="client", id="client"),
        session_id=uuid4(),
        encoding="utf-8",
        data_digest=hashlib.sha256(text.encode()).hexdigest(),
        byte_count=len(text.encode()),
        lease_id=uuid4(),
        lease_fence=fence,
    )


def permission_service(
    connection: sqlite3.Connection,
    *,
    now: datetime = NOW,
    ttl: timedelta = timedelta(seconds=30),
) -> tuple[PermissionService, PermissionStore]:
    store = PermissionStore(connection)
    return (
        PermissionService(
            store=store,
            policy=LocalServicePermissionPolicy(),
            clock=lambda: now,
            grant_ttl=ttl,
        ),
        store,
    )


def request_meta() -> RequestMeta:
    request_id = uuid4()
    return RequestMeta(
        request_id=request_id,
        correlation=Correlation(correlation_id=request_id),
    )


def writer_lease(
    *,
    session_id: UUID,
    holder: PrincipalRef,
    fence: int = 1,
) -> WriterLease:
    return WriterLease(
        lease_id=uuid4(),
        resource=LeaseResource(session_id=session_id),
        holder=holder,
        mode=WriterMode.RAW_TERMINAL,
        fence=fence,
        issued_at=NOW,
        renewed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )


def test_operation_digest_binds_parameters_and_lease_fence() -> None:
    proposed = operation()
    changed_digest = hashlib.sha256(b"changed").hexdigest()
    assert operation_digest(proposed) != operation_digest(
        proposed.model_copy(update={"data_digest": changed_digest})
    )
    assert operation_digest(proposed) != operation_digest(
        proposed.model_copy(update={"lease_fence": 5})
    )


def test_policy_allow_persists_decision_grant_and_single_use() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    proposed = operation()

    proof = service.request(proposed)
    service.enforce(proposed, proof)

    assert store.count("permission_policy_decisions") == 1
    assert store.count("permission_authorization_grants") == 1
    assert store.count("permission_authorization_uses") == 1
    assert [fact.kind for fact in replay_facts(connection)] == [
        "permission.grant.issued",
        "permission.grant.used",
    ]
    permission_inputs = replay_projection_inputs(
        connection,
        projection="permissions",
    )
    assert [item.generation for item in permission_inputs] == [0, 1]
    with pytest.raises(InvalidAuthorizationError, match="use limit|already used"):
        service.enforce(proposed, proof)


def test_grant_use_limit_is_consumed_atomically_across_connections(
    tmp_path: Path,
) -> None:
    database = tmp_path / "permissions.db"
    issuing = sqlite3.connect(database, timeout=5)
    service, _ = permission_service(issuing)
    proposed = operation()
    proof = service.request(proposed)
    second_proof = proof.model_copy(update={"authorization_id": uuid4()})
    first_connection = sqlite3.connect(database, timeout=5, check_same_thread=False)
    second_connection = sqlite3.connect(database, timeout=5, check_same_thread=False)
    first, _ = permission_service(first_connection)
    second, _ = permission_service(second_connection)

    def enforce(
        candidate: PermissionService,
        authorization: AuthorizationProof,
    ) -> bool:
        try:
            candidate.enforce(proposed, authorization)
        except InvalidAuthorizationError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda item: enforce(*item),
                ((first, proof), (second, second_proof)),
            )
        )
    assert sorted(outcomes) == [False, True]
    assert (
        issuing.execute(
            "SELECT COUNT(*) FROM permission_authorization_uses"
        ).fetchone()[0]
        == 1
    )


def test_permission_use_and_revocation_roll_back_when_fact_append_fails() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    proposed = operation()
    proof = service.request(proposed)
    connection.execute(
        """
        CREATE TRIGGER reject_permission_outcome
        BEFORE INSERT ON retained_facts
        WHEN NEW.kind IN ('permission.grant.used', 'permission.grant.revoked')
        BEGIN
            SELECT RAISE(ABORT, 'reject permission outcome');
        END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="reject permission outcome"):
        service.enforce(proposed, proof)
    assert store.grant_use_count(proof.grant_id) == 0
    with pytest.raises(sqlite3.IntegrityError, match="reject permission outcome"):
        store.revoke_grant(proof.grant_id, revoked_at=NOW, reason="rollback")
    assert not store.grant_is_revoked(proof.grant_id)

    connection.execute("DROP TRIGGER reject_permission_outcome")
    service.enforce(proposed, proof)
    assert store.grant_use_count(proof.grant_id) == 1


def test_proof_rejects_digest_expiry_and_revocation() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    proposed = operation()
    proof = service.request(proposed)
    changed = proposed.model_copy(
        update={"data_digest": hashlib.sha256(b"different").hexdigest()}
    )
    with pytest.raises(InvalidAuthorizationError, match="digest"):
        service.enforce(changed, proof)

    store.revoke_grant(proof.grant_id, revoked_at=NOW, reason="review withdrawn")
    assert [fact.kind for fact in replay_facts(connection)] == [
        "permission.grant.issued",
        "permission.grant.revoked",
    ]
    with pytest.raises(InvalidAuthorizationError, match="revoked"):
        service.enforce(proposed, proof)

    expired_service, _ = permission_service(
        connection,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(InvalidAuthorizationError, match="expired|revoked"):
        expired_service.enforce(proposed, proof)


def test_file_paths_are_canonical_and_traversal_free() -> None:
    with pytest.raises(ValidationError, match="traversal-free"):
        FileMutation(
            operation_id=uuid4(),
            principal=PermissionPrincipal(kind="service", id="writer"),
            repository_id=uuid4(),
            path="allowed/../outside",
            action="delete",
        )


def test_tool_arguments_are_policy_visible_and_digest_bound() -> None:
    proposed = ToolInvocation(
        operation_id=uuid4(),
        principal=PermissionPrincipal(kind="service", id="tool-runner"),
        tool=" shell ",
        arguments={"command": "printf safe", "timeout_seconds": 3},
    )
    assert proposed.tool == "shell"
    assert proposed.arguments["command"] == "printf safe"
    with pytest.raises(ValidationError, match="digest"):
        ToolInvocation(
            operation_id=proposed.operation_id,
            principal=proposed.principal,
            tool=proposed.tool,
            arguments=proposed.arguments,
            arguments_digest="0" * 64,
        )


def test_takeover_requires_persisted_approval_evidence() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    holder = principal(PrincipalKind.SERVICE, "scheduler")
    request = AcquireWriterLease(
        meta=request_meta(),
        session_id=uuid4(),
        mode=WriterMode.STRUCTURED,
        force=True,
    )
    proposed = normalize_writer_takeover(
        request,
        holder,
        writer_lease(
            session_id=request.session_id,
            holder=principal(PrincipalKind.USER, "human"),
        ),
    )

    with pytest.raises(ApprovalRequiredError) as required:
        service.request(proposed)
    proof = service.approve(
        proposed,
        decision=required.value.decision,
        request=required.value.request,
        reviewer=PermissionPrincipal(kind="reviewer", id="reviewer"),
        choice=ApprovalChoice.APPROVE,
    )
    service.enforce(proposed, proof)

    assert store.count("permission_approval_requests") == 1
    assert store.count("permission_approval_evidence") == 1
    assert store.count("permission_authorization_grants") == 1


def test_standalone_denial_is_authoritative_and_fabricated_prior_evidence_is_rejected() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    requester = principal(PrincipalKind.SERVICE, "scheduler")
    request = AcquireWriterLease(
        meta=request_meta(),
        session_id=uuid4(),
        mode=WriterMode.STRUCTURED,
        force=True,
    )
    proposed = normalize_writer_takeover(
        request,
        requester,
        writer_lease(
            session_id=request.session_id,
            holder=principal(PrincipalKind.USER, "human"),
        ),
    )
    with pytest.raises(ApprovalRequiredError) as required:
        service.request(proposed)
    fake = required.value.request.model_copy()
    with pytest.raises(InvalidAuthorizationError, match="loaded from persistence"):
        service.approve(
            proposed,
            decision=required.value.decision,
            request=required.value.request,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.APPROVE,
            prior_decisions=(
                # The specific value is irrelevant: callers may not contribute
                # authorization evidence.
                fake,  # type: ignore[arg-type]
            ),
        )
    with pytest.raises(PermissionDeniedError, match="approval is denied"):
        service.approve(
            proposed,
            decision=required.value.decision,
            request=required.value.request,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.DENY,
        )
    with pytest.raises(ValueError, match="already denied"):
        service.approve(
            proposed,
            decision=required.value.decision,
            request=required.value.request,
            reviewer=PermissionPrincipal(kind="reviewer", id="human"),
            choice=ApprovalChoice.APPROVE,
        )
    assert store.count("permission_authorization_grants") == 0


def test_authenticated_product_handler_resolves_standalone_takeover() -> None:
    connection = sqlite3.connect(":memory:")
    service, _ = permission_service(
        connection,
        ttl=timedelta(days=365),
    )
    requester = principal(PrincipalKind.SERVICE, "scheduler")
    request = AcquireWriterLease(
        meta=request_meta(),
        session_id=uuid4(),
        mode=WriterMode.STRUCTURED,
        force=True,
    )
    proposed = normalize_writer_takeover(
        request,
        requester,
        writer_lease(
            session_id=request.session_id,
            holder=principal(PrincipalKind.USER, "human"),
            fence=7,
        ),
    )
    with pytest.raises(ApprovalRequiredError) as required:
        service.request(proposed)

    class Host:
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(db=connection)
            self.handlers: dict[str, Any] = {}

        def register_rpc_handler(self, name: str, handler: Any) -> None:
            self.handlers[name] = handler

    host = Host()
    approval_handlers.register(host)  # type: ignore[arg-type]
    result = host.handlers["approval.decide"](
        {
            "approval_id": str(required.value.request.approval_id),
            "expected_operation_digest": operation_digest(proposed),
            "choice": "approve",
            "rationale": "authenticated review",
            "reviewer": {"kind": "client", "id": "tui-7"},
        }
    )
    authorization = AuthorizationProof.model_validate(result["authorization"])
    assert authorization.operation_id == proposed.operation_id
    assert authorization.lease_fence == proposed.current_lease_fence


async def test_force_takeover_boolean_cannot_bypass_production_authorizer() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    permission, _ = permission_service(connection)
    authorizer = SessionPermissionAuthorizer(permission)
    session_id = uuid4()
    record = HarnessSessionRecord(
        session_id=session_id,
        repository_id=uuid4(),
        harness="terminal-only",
        transport=SessionTransport.TMUX,
        transport_ref="test",
        status=SessionStatus.READY,
        revision=0,
        capabilities=SessionCapabilities(raw_terminal=True),
        started_at=NOW,
    )
    controller = SessionController(
        record=record,
        store=SessionStore(connection),
        backend=RecordingBackend(),
        authorizer=authorizer,
        takeover_authorizer=lambda request, holder, current, proof: authorizer.authorize_takeover(
            request,
            holder=holder,
            current_lease=current,
            authorization=proof,
        ),
    )
    human = principal(PrincipalKind.USER, "human")
    initial = await controller.acquire_writer_lease(
        AcquireWriterLease(
            meta=request_meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(initial, WriterLeaseGranted)
    service_holder = principal(PrincipalKind.SERVICE, "scheduler")
    request = AcquireWriterLease(
        meta=request_meta(),
        session_id=session_id,
        mode=WriterMode.STRUCTURED,
        force=True,
    )
    denied = await controller.acquire_writer_lease(
        request,
        holder=service_holder,
        force_authorized=True,
    )
    assert isinstance(denied, WriterLeaseDenied)

    proposed = normalize_writer_takeover(request, service_holder, initial.lease)
    with pytest.raises(ApprovalRequiredError) as required:
        permission.request(proposed)
    proof = permission.approve(
        proposed,
        decision=required.value.decision,
        request=required.value.request,
        reviewer=PermissionPrincipal(kind="reviewer", id="human-reviewer"),
        choice=ApprovalChoice.APPROVE,
    )
    released = await controller.release_writer_lease(
        ReleaseWriterLease(
            meta=request_meta(),
            lease_id=initial.lease.lease_id,
            fence=initial.lease.fence,
        ),
        holder=human,
    )
    assert isinstance(released, WriterLeaseGranted)
    replacement_holder = principal(PrincipalKind.USER, "other-human")
    replacement = await controller.acquire_writer_lease(
        AcquireWriterLease(
            meta=request_meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=replacement_holder,
    )
    assert isinstance(replacement, WriterLeaseGranted)
    stale = await controller.acquire_writer_lease(
        request,
        holder=service_holder,
        authorization=proof,
    )
    assert isinstance(stale, WriterLeaseDenied)

    current = normalize_writer_takeover(request, service_holder, replacement.lease)
    with pytest.raises(ApprovalRequiredError) as current_required:
        permission.request(current)
    current_proof = permission.approve(
        current,
        decision=current_required.value.decision,
        request=current_required.value.request,
        reviewer=PermissionPrincipal(kind="reviewer", id="human-reviewer"),
        choice=ApprovalChoice.APPROVE,
    )
    takeover = await controller.acquire_writer_lease(
        request,
        holder=service_holder,
        authorization=current_proof,
    )
    assert isinstance(takeover, WriterLeaseGranted)
    await controller.close()


async def test_non_session_wrapper_enforces_exact_file_mutation_before_effect() -> None:
    connection = sqlite3.connect(":memory:")
    service, _ = permission_service(connection)
    operation = FileMutation(
        operation_id=uuid4(),
        principal=PermissionPrincipal(kind="service", id="file-writer"),
        repository_id=uuid4(),
        path="src/app.py",
        action="write",
        content_digest=hashlib.sha256(b"new content").hexdigest(),
    )
    proof = service.request(operation)
    called = False

    async def effect() -> str:
        nonlocal called
        called = True
        return "written"

    assert (
        await SideEffectEnforcer(service).execute(
            operation,
            effect,
            authorization=proof,
        )
        == "written"
    )
    assert called

    called = False
    tampered = operation.model_copy(update={"path": "src/other.py"})
    tampered_proof = service.request(operation)
    with pytest.raises(InvalidAuthorizationError, match="digest"):
        await SideEffectEnforcer(service).execute(
            tampered,
            effect,
            authorization=tampered_proof,
        )
    assert not called


async def test_controller_validates_typed_proof_immediately_before_terminal_write() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    session_store = SessionStore(connection)
    permission, _ = permission_service(connection)
    authorizer = SessionPermissionAuthorizer(permission)
    session_id = uuid4()
    record = HarnessSessionRecord(
        session_id=session_id,
        repository_id=uuid4(),
        harness="terminal-only",
        transport=SessionTransport.TMUX,
        transport_ref="test",
        status=SessionStatus.READY,
        revision=0,
        capabilities=SessionCapabilities(raw_terminal=True),
        started_at=NOW,
    )
    backend = RecordingBackend()
    controller = SessionController(
        record=record,
        store=session_store,
        backend=backend,
        authorizer=authorizer,
    )
    client = principal()
    granted = await controller.acquire_writer_lease(
        AcquireWriterLease(
            meta=request_meta(),
            session_id=session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=client,
    )
    assert isinstance(granted, WriterLeaseGranted)
    command = WriteTerminalInput(
        operation_id=uuid4(),
        lease_id=granted.lease.lease_id,
        fence=granted.lease.fence,
        data="authorized",
    )
    proposed = normalize_session_command(command, controller.record, client)
    proof = permission.request(proposed)

    await controller.execute(command, principal=client, authorization=proof)
    assert backend.writes == [b"authorized"]

    altered = command.model_copy(update={"data": "tampered"})
    altered_proof = permission.request(
        normalize_session_command(command, controller.record, client)
    )
    with pytest.raises(SessionAuthorizationError):
        await controller.execute(altered, principal=client, authorization=altered_proof)
    assert backend.writes == [b"authorized"]
    await controller.close()


def test_harness_shell_request_maps_to_tool_invocation() -> None:
    proposed = normalize_harness_permission_request(
        {
            "request_id_hint": "perm-1",
            "tool_name": "shell",
            "command": "rm -rf temporary",
            "description": "Clean generated files",
            "risk_attributes": ["shell"],
        },
        principal=PermissionPrincipal(kind="llm", id="agent"),
    )
    assert isinstance(proposed, ToolInvocation)
    assert proposed.tool == "shell"
    assert proposed.arguments["command"] == "rm -rf temporary"
    assert proposed.arguments["risk_attributes"] == ["shell"]


def test_harness_file_request_maps_to_file_mutation_when_path_known() -> None:
    repository_id = uuid4()
    proposed = normalize_harness_permission_request(
        {
            "tool_name": "edit",
            "command": "src/main.py",
            "description": "Update entrypoint",
            "risk_attributes": ["write"],
        },
        principal=PermissionPrincipal(kind="client", id="ui"),
        repository_id=repository_id,
    )
    assert isinstance(proposed, FileMutation)
    assert proposed.path == "src/main.py"
    assert proposed.action == "write"
    assert proposed.repository_id == repository_id


@pytest.mark.parametrize("kind", ("client", "llm", "user"))
def test_harness_bridge_requires_approval_for_tool_from_client_llm_user(
    kind: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)

    with pytest.raises(ApprovalRequiredError) as required:
        request_harness_permission(
            service,
            {
                "tool_name": "Bash",
                "command": "pytest -q",
                "description": "Run unit tests",
                "risk_attributes": ["shell"],
            },
            principal=PermissionPrincipal(kind=kind, id=f"{kind}-actor"),
        )

    assert required.value.request.status == "pending"
    assert "tool.invoke" in required.value.decision.reason
    assert store.count("permission_approval_requests") == 1
    assert store.count("permission_policy_decisions") == 1
    assert store.count("permission_authorization_grants") == 0


def test_harness_bridge_maps_permission_request_state_and_allows_service() -> None:
    from murder.llm.harness_control.model.observations import (
        ChoiceState,
        PermissionRequestState,
    )

    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    observed = PermissionRequestState(
        "permission-1",
        "shell",
        "printf safe",
        "Echo",
        (ChoiceState("allow_once", "Allow once", disabled=False),),
        "allow_once",
        frozenset({"shell"}),
    )

    proof = request_harness_permission(
        service,
        observed,
        principal=PermissionPrincipal(kind="service", id="harness-bridge"),
    )
    assert isinstance(proof, AuthorizationProof)
    assert store.count("permission_authorization_grants") == 1


def test_pending_approval_is_findable_by_operation_id() -> None:
    connection = sqlite3.connect(":memory:")
    service, store = permission_service(connection)
    operation_id = uuid4()

    with pytest.raises(ApprovalRequiredError) as required:
        request_harness_permission(
            service,
            {
                "tool_name": "shell",
                "command": "ls",
                "description": "List",
                "risk_attributes": ["shell"],
            },
            principal=PermissionPrincipal(kind="llm", id="agent-1"),
            operation_id=operation_id,
        )

    pending = store.get_pending_approval_for_operation(operation_id)
    assert pending is not None
    decision, approval = pending
    assert decision.decision_id == required.value.decision.decision_id
    assert approval.approval_id == required.value.request.approval_id
    assert approval.status == "pending"

    proof = service.decide_approval(
        normalize_harness_permission_request(
            {
                "tool_name": "shell",
                "command": "ls",
                "description": "List",
                "risk_attributes": ["shell"],
            },
            principal=PermissionPrincipal(kind="llm", id="agent-1"),
            operation_id=operation_id,
        ),
        decision=decision,
        request=approval,
        reviewer=PermissionPrincipal(kind="user", id="reviewer"),
        choice=ApprovalChoice.APPROVE,
    )
    assert isinstance(proof, AuthorizationProof)
    assert store.get_pending_approval_for_operation(operation_id) is None
