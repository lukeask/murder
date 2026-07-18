from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from murder.llm.harness_control.runtime.manual_input import emit_manual_input
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.runtime.sessions.capabilities import verified_tmux_capabilities
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    InterruptSession,
    PrincipalKind,
    PrincipalRef,
    RequestMeta,
    ResizeTerminal,
    SendStructuredMessage,
    SessionCapabilities,
    SessionStatus,
    SessionTransport,
    TerminateSession,
    WriterLeaseGranted,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.controller import (
    SessionAuthorizationError,
    SessionCapabilityError,
    SessionController,
)
from murder.runtime.sessions.persistence import (
    SessionRevisionConflictError,
    SessionStore,
    StaleWriterLeaseError,
    WriterLeaseRequiredError,
    ensure_session_schema,
)
from murder.runtime.sessions.registry import (
    SessionControllerRegistry,
    close_registry_for_connection,
)

SECOND_REVISION = 2


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.first_entered = asyncio.Event()
        self.release_first = asyncio.Event()
        self.block_first = False
        self.block_raw = False
        self.raw_entered = asyncio.Event()
        self.release_raw = asyncio.Event()
        self.fail_termination = False

    async def recover(self, record: HarnessSessionRecord) -> None:
        self.calls.append(("recover", record.session_id))

    async def send_structured_message(self, command: SendStructuredMessage) -> None:
        self.calls.append(("structured", command.text))
        if self.block_first and len(self.calls) == 1:
            self.first_entered.set()
            await self.release_first.wait()

    async def write_terminal_input(self, command: WriteTerminalInput, data: bytes) -> None:
        self.calls.append(("raw", data))
        if self.block_raw:
            self.raw_entered.set()
            await self.release_raw.wait()

    async def resize_terminal(self, command: ResizeTerminal) -> None:
        self.calls.append(("resize", (command.columns, command.rows)))

    async def interrupt(self, command: InterruptSession) -> None:
        self.calls.append(("interrupt", command.reason))

    async def terminate(self, command: TerminateSession) -> None:
        self.calls.append(("terminate", command.force))
        if self.fail_termination:
            raise RuntimeError("termination failed")


async def allow_test_command(*_args: object) -> bool:
    return True


@pytest.mark.parametrize("harness", ["codex", "claude_code", "antigravity"])
def test_verified_tmux_capabilities_include_supported_approvals(harness: str) -> None:
    capabilities = verified_tmux_capabilities(harness)
    assert capabilities.structured_messages
    assert capabilities.structured_approvals
    assert capabilities.raw_terminal


@pytest.mark.parametrize("harness", ["cursor", "pi"])
def test_verified_tmux_capabilities_do_not_overclaim_approvals(harness: str) -> None:
    capabilities = verified_tmux_capabilities(harness)
    assert capabilities.structured_messages
    assert not capabilities.structured_approvals
    assert capabilities.raw_terminal


def meta() -> RequestMeta:
    return RequestMeta(
        request_id=uuid4(),
        correlation=Correlation(correlation_id=uuid4()),
    )


def session_record(
    session_id: UUID,
    *,
    capabilities: SessionCapabilities | None = None,
) -> HarnessSessionRecord:
    return HarnessSessionRecord(
        session_id=session_id,
        repository_id=uuid4(),
        harness="codex",
        transport=SessionTransport.TMUX,
        transport_ref="murder_test",
        status=SessionStatus.READY,
        revision=0,
        capabilities=capabilities
        or SessionCapabilities(structured_messages=True, raw_terminal=True),
        started_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )


def setup_controller(
    *,
    capabilities: SessionCapabilities | None = None,
) -> tuple[SessionController, SessionStore, RecordingBackend, HarnessSessionRecord]:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    store = SessionStore(connection)
    record = session_record(uuid4(), capabilities=capabilities)
    backend = RecordingBackend()
    return (
        SessionController(
            record=record,
            store=store,
            backend=backend,
            authorizer=allow_test_command,
        ),
        store,
        backend,
        record,
    )


async def test_mailbox_serializes_commands_and_advances_revision() -> None:
    controller, _, backend, record = setup_controller()
    backend.block_first = True
    principal = PrincipalRef(kind=PrincipalKind.USER, id="human")
    first = asyncio.create_task(
        controller.execute(
            SendStructuredMessage(operation_id=uuid4(), text="one"),
            principal=principal,
            expected_revision=0,
        )
    )
    await backend.first_entered.wait()
    second = asyncio.create_task(
        controller.execute(
            ResizeTerminal(operation_id=uuid4(), columns=120, rows=40),
            principal=principal,
            expected_revision=1,
        )
    )
    await asyncio.sleep(0)
    assert backend.calls == [("structured", "one")]
    backend.release_first.set()
    first_receipt, second_receipt = await asyncio.gather(first, second)
    assert first_receipt.revision == 1
    assert second_receipt.revision == SECOND_REVISION
    assert backend.calls == [("structured", "one"), ("resize", (120, 40))]
    assert controller.record.session_id == record.session_id
    await controller.close()


async def test_controller_default_denies_commands_without_an_explicit_policy() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    store = SessionStore(connection)
    record = session_record(uuid4())
    backend = RecordingBackend()
    controller = SessionController(record=record, store=store, backend=backend)
    with pytest.raises(SessionAuthorizationError):
        await controller.execute(
            ResizeTerminal(operation_id=uuid4(), columns=80, rows=24),
            principal=PrincipalRef(kind=PrincipalKind.USER, id="human"),
        )
    assert backend.calls == []
    await controller.close()


def test_mailbox_restarts_after_owning_event_loop_is_closed() -> None:
    controller, _, backend, _ = setup_controller()
    principal = PrincipalRef(kind=PrincipalKind.SERVICE, id="service")

    first = asyncio.run(
        controller.execute(
            SendStructuredMessage(operation_id=uuid4(), text="first loop"),
            principal=principal,
        )
    )
    second = asyncio.run(
        controller.execute(
            SendStructuredMessage(operation_id=uuid4(), text="second loop"),
            principal=principal,
        )
    )

    assert first.revision == 1
    assert second.revision == SECOND_REVISION
    assert backend.calls == [
        ("structured", "first loop"),
        ("structured", "second loop"),
    ]
    asyncio.run(controller.close())


async def test_raw_write_revalidates_fence_after_queued_takeover() -> None:
    controller, store, backend, record = setup_controller()
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    workflow = PrincipalRef(kind=PrincipalKind.WORKFLOW, id="workflow")
    lease = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(lease, WriterLeaseGranted)
    takeover = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.STRUCTURED,
            force=True,
        ),
        holder=workflow,
        force_authorized=True,
    )
    assert isinstance(takeover, WriterLeaseGranted)

    with pytest.raises(StaleWriterLeaseError):
        await controller.execute(
            WriteTerminalInput(
                operation_id=uuid4(),
                lease_id=lease.lease.lease_id,
                fence=lease.lease.fence,
                data="late input",
            ),
            principal=human,
        )
    assert backend.calls == []
    await controller.close()


async def test_force_takeover_cannot_race_a_physically_blocked_write() -> None:
    controller, store, backend, record = setup_controller()
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    workflow = PrincipalRef(kind=PrincipalKind.WORKFLOW, id="workflow")
    granted = await controller.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(granted, WriterLeaseGranted)
    backend.block_raw = True
    write = asyncio.create_task(
        controller.execute(
            WriteTerminalInput(
                operation_id=uuid4(),
                lease_id=granted.lease.lease_id,
                fence=granted.lease.fence,
                data="in flight",
            ),
            principal=human,
        )
    )
    await backend.raw_entered.wait()
    takeover = asyncio.create_task(
        controller.acquire_writer_lease(
            AcquireWriterLease(
                meta=meta(),
                session_id=record.session_id,
                mode=WriterMode.STRUCTURED,
                force=True,
            ),
            holder=workflow,
            force_authorized=True,
        )
    )
    await asyncio.sleep(0)
    assert not takeover.done()
    active_during_write = store.active_writer_lease(record.session_id)
    assert active_during_write is not None
    assert active_during_write.holder == human
    backend.release_raw.set()
    await write
    taken = await takeover
    assert isinstance(taken, WriterLeaseGranted)
    assert taken.lease.holder == workflow
    assert backend.calls == [("raw", b"in flight")]
    await controller.close()


async def test_human_raw_lease_blocks_automated_structured_input() -> None:
    controller, store, backend, record = setup_controller()
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    with pytest.raises(WriterLeaseRequiredError):
        await controller.execute(
            SendStructuredMessage(operation_id=uuid4(), text="automation"),
            principal=PrincipalRef(kind=PrincipalKind.WORKFLOW, id="workflow"),
        )
    assert backend.calls == []
    await controller.close()


async def test_revision_and_capability_are_checked_before_effect() -> None:
    controller, _, backend, _ = setup_controller(
        capabilities=SessionCapabilities(
            structured_messages=False,
            raw_terminal=True,
        )
    )
    principal = PrincipalRef(kind=PrincipalKind.USER, id="human")
    with pytest.raises(SessionRevisionConflictError):
        await controller.execute(
            ResizeTerminal(operation_id=uuid4(), columns=80, rows=24),
            principal=principal,
            expected_revision=9,
        )
    with pytest.raises(SessionCapabilityError):
        await controller.execute(
            SendStructuredMessage(operation_id=uuid4(), text="unsupported"),
            principal=principal,
        )
    assert backend.calls == []
    await controller.close()


async def test_existing_verified_capability_runs_inside_same_mailbox() -> None:
    controller, _, backend, _ = setup_controller(
        capabilities=SessionCapabilities(
            structured_messages=True,
            structured_approvals=True,
            raw_terminal=True,
        )
    )
    principal = PrincipalRef(kind=PrincipalKind.SERVICE, id="service")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def capability() -> str:
        entered.set()
        await release.wait()
        return "verified"

    internal = asyncio.create_task(
        controller.run_internal(
            uuid4(),
            capability,
            principal=principal,
            required_capability="structured_approvals",
            expected_revision=0,
        )
    )
    await entered.wait()
    resize = asyncio.create_task(
        controller.execute(
            ResizeTerminal(operation_id=uuid4(), columns=90, rows=30),
            principal=principal,
            expected_revision=1,
        )
    )
    await asyncio.sleep(0)
    assert backend.calls == []
    release.set()
    assert await internal == "verified"
    assert (await resize).revision == SECOND_REVISION
    assert backend.calls == [("resize", (90, 30))]
    await controller.close()


async def test_terminate_revokes_outstanding_writer_and_stops_session() -> None:
    controller, store, backend, record = setup_controller()
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    granted = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(granted, WriterLeaseGranted)

    receipt = await controller.execute(
        TerminateSession(operation_id=uuid4(), reason="test shutdown"),
        principal=PrincipalRef(kind=PrincipalKind.SERVICE, id="service"),
    )

    assert receipt.revision == SECOND_REVISION
    assert controller.record.status is SessionStatus.STOPPED
    assert store.active_writer_lease(record.session_id) is None
    with pytest.raises(StaleWriterLeaseError):
        store.validate_writer_lease(
            session_id=record.session_id,
            lease_id=granted.lease.lease_id,
            fence=granted.lease.fence,
            holder=human,
        )
    assert backend.calls == [("terminate", False)]
    await controller.close()


async def test_termination_persists_stopping_then_failed_on_backend_failure() -> None:
    controller, store, backend, record = setup_controller()
    backend.fail_termination = True
    seen_statuses: list[SessionStatus] = []
    original_terminate = backend.terminate

    async def inspect_stopping(command: TerminateSession) -> None:
        persisted = store.get_session(record.session_id)
        assert persisted is not None
        seen_statuses.append(persisted.status)
        await original_terminate(command)

    backend.terminate = inspect_stopping  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="termination failed"):
        await controller.execute(
            TerminateSession(operation_id=uuid4()),
            principal=PrincipalRef(kind=PrincipalKind.SERVICE, id="service"),
        )
    assert seen_statuses == [SessionStatus.STOPPING]
    assert controller.record.status is SessionStatus.FAILED
    assert controller.record.last_observed_at is not None
    assert controller.record.stopped_at is not None
    await controller.close()


@pytest.mark.parametrize(
    "status",
    [
        SessionStatus.READY,
        SessionStatus.WORKING,
        SessionStatus.AWAITING_INPUT,
        SessionStatus.AWAITING_APPROVAL,
    ],
)
async def test_observation_status_and_timestamp_are_serialized(
    status: SessionStatus,
) -> None:
    controller, _, _, _ = setup_controller()
    observed_at = datetime(2026, 7, 18, 1, tzinfo=timezone.utc)
    observed = await controller.observe(
        status,
        observed_at=observed_at,
    )
    assert observed.status is status
    assert observed.last_observed_at == observed_at
    assert observed.revision == 1
    await controller.close()


async def test_registry_returns_one_controller_and_recovers_persisted_record() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)
    store = SessionStore(connection)
    record = session_record(uuid4())
    store.save_session(record)
    backends: list[RecordingBackend] = []

    def backend_factory(_record: HarnessSessionRecord) -> RecordingBackend:
        backend = RecordingBackend()
        backends.append(backend)
        return backend

    registry = SessionControllerRegistry(store=store, backend_factory=backend_factory)
    first, second = await asyncio.gather(
        registry.get_or_create(record.session_id),
        registry.get_or_create(record.session_id),
    )
    assert first is second
    assert len(backends) == 1
    await registry.close()

    recovered_registry = SessionControllerRegistry(
        store=store,
        backend_factory=backend_factory,
    )
    recovered = await recovered_registry.recover_persisted()
    assert len(recovered) == 1
    assert backends[-1].calls == [("recover", record.session_id)]
    await recovered_registry.close()


async def test_duplicate_live_controls_share_the_persisted_session_controller() -> None:
    connection = sqlite3.connect(":memory:")
    ensure_session_schema(connection)

    def bare_control() -> VerifiedHarnessControlSession:
        control = object.__new__(VerifiedHarnessControlSession)
        control._connection = connection
        control._persistence_session_id = "agent-1"
        control._session_controller = None
        control._session_controller_registry = None
        control._session_store = None
        control.harness_id = "codex"
        control.terminal_session = "tmux-agent-1"
        return control

    first_control = bare_control()
    second_control = bare_control()
    first, second = await asyncio.gather(
        first_control.ensure_session_controller(
            repository_key="repo",
            agent_key="agent-1",
        ),
        second_control.ensure_session_controller(
            repository_key="repo",
            agent_key="agent-1",
        ),
    )
    assert first is second
    await close_registry_for_connection(connection)
    connection.close()


async def test_bound_legacy_manual_input_cannot_bypass_controller_or_fence() -> None:
    controller, store, backend, record = setup_controller()
    human = PrincipalRef(kind=PrincipalKind.USER, id="human")
    granted = store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
        ),
        holder=human,
    )
    assert isinstance(granted, WriterLeaseGranted)
    control = object.__new__(VerifiedHarnessControlSession)
    control._session_controller_binding = None
    control.bind_session_controller(controller, lease=granted.lease)

    receipt = await emit_manual_input(
        control,
        text="hello",
        literal=True,
        append_enter=True,
    )
    assert receipt.accepted_by_terminal_transport
    assert backend.calls == [("raw", b"hello\r")]

    store.acquire_writer_lease(
        AcquireWriterLease(
            meta=meta(),
            session_id=record.session_id,
            mode=WriterMode.RAW_TERMINAL,
            force=True,
        ),
        holder=PrincipalRef(kind=PrincipalKind.CLIENT, id="other"),
        force_authorized=True,
    )
    with pytest.raises(StaleWriterLeaseError):
        await emit_manual_input(
            control,
            text="late",
            literal=True,
            append_enter=False,
        )
    assert backend.calls == [("raw", b"hello\r")]
    await controller.close()
