"""One serialized mailbox for every mutation of a live harness session."""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Literal, Protocol, TypeVar, cast
from uuid import UUID

from murder.permissions.contracts import AuthorizationProof
from murder.runtime.sessions.backend import SessionBackend
from murder.runtime.sessions.contracts import (
    AcquireWriterLease,
    Correlation,
    HarnessSessionRecord,
    InterruptSession,
    PrincipalKind,
    PrincipalRef,
    ReleaseWriterLease,
    RenewWriterLease,
    ResizeTerminal,
    SendStructuredMessage,
    SessionCommand,
    SessionCommandReceipt,
    SessionStatus,
    TerminateSession,
    WriterLease,
    WriterLeaseReply,
    WriterMode,
    WriteTerminalInput,
)
from murder.runtime.sessions.persistence import (
    SessionNotFoundError,
    SessionRevisionConflictError,
    SessionStore,
    StaleWriterLeaseError,
    WriterLeaseRequiredError,
)

ResultT = TypeVar("ResultT")


class SessionControllerError(RuntimeError):
    pass


class SessionControllerClosedError(SessionControllerError):
    pass


class SessionCapabilityError(SessionControllerError):
    pass


class SessionAuthorizationError(SessionControllerError):
    pass


class SessionCommandAuthorizer(Protocol):
    async def __call__(
        self,
        command: SessionCommand,
        record: HarnessSessionRecord,
        principal: PrincipalRef,
        authorization: AuthorizationProof | None,
    ) -> bool: ...


async def _deny_authorized_command(
    command: SessionCommand,
    record: HarnessSessionRecord,
    principal: PrincipalRef,
    authorization: AuthorizationProof | None,
) -> bool:
    del command, record, principal, authorization
    return False


async def trusted_local_session_authorizer(
    command: SessionCommand,
    record: HarnessSessionRecord,
    principal: PrincipalRef,
    authorization: AuthorizationProof | None,
) -> bool:
    """Explicit policy for the trusted in-process service boundary.

    Automation principals may use supported commands. A raw-terminal command
    is authorized by its separately validated holder-bound fenced lease.
    Human/client callers need a policy supplied by their API boundary for
    every other command. Under trusted-local deployment that boundary uses a
    claimed client id, not an authenticated principal.
    """

    del record, authorization
    return principal.kind in {PrincipalKind.SERVICE, PrincipalKind.WORKFLOW} or isinstance(
        command, WriteTerminalInput
    )


CooperativeInputPolicy = Callable[
    [HarnessSessionRecord, PrincipalRef, PrincipalRef],
    Awaitable[bool],
]


async def _deny_cooperative_input(
    record: HarnessSessionRecord,
    lease_holder: PrincipalRef,
    command_principal: PrincipalRef,
) -> bool:
    del record, lease_holder, command_principal
    return False


@dataclass(slots=True)
class _MailboxItem(Generic[ResultT]):
    command: SessionCommand
    principal: PrincipalRef
    expected_revision: int | None
    authorization: AuthorizationProof | None
    result: asyncio.Future[ResultT]


InternalSessionCapability = Literal[
    "structured_messages",
    "structured_approvals",
    "model_switching",
]


@dataclass(slots=True)
class _InternalMailboxItem(Generic[ResultT]):
    operation_id: UUID
    effect: Callable[[], Awaitable[ResultT]]
    principal: PrincipalRef
    expected_revision: int | None
    required_capability: InternalSessionCapability
    result: asyncio.Future[ResultT]


@dataclass(slots=True)
class _LeaseMailboxItem:
    operation: Literal["acquire", "renew", "release"]
    request: AcquireWriterLease | RenewWriterLease | ReleaseWriterLease
    holder: PrincipalRef
    force_authorized: bool
    authorization: AuthorizationProof | None
    reason: str | None
    result: asyncio.Future[WriterLeaseReply]


@dataclass(slots=True)
class _ObservationMailboxItem:
    status: SessionStatus
    observed_at: datetime
    result: asyncio.Future[HarnessSessionRecord]


_STOP = object()


class SessionController:
    """Serializes a session and revalidates all guards at the effect boundary."""

    def __init__(
        self,
        *,
        record: HarnessSessionRecord,
        store: SessionStore,
        backend: SessionBackend,
        authorizer: SessionCommandAuthorizer = _deny_authorized_command,
        takeover_authorizer: Callable[
            [
                AcquireWriterLease,
                PrincipalRef,
                WriterLease,
                AuthorizationProof | None,
            ],
            bool,
        ]
        | None = None,
        cooperative_input_policy: CooperativeInputPolicy = _deny_cooperative_input,
    ) -> None:
        self._session_id = record.session_id
        self._store = store
        self._backend = backend
        self._authorizer = authorizer
        self._takeover_authorizer = takeover_authorizer
        self._cooperative_input_policy = cooperative_input_policy
        self._mailbox: asyncio.Queue[
            _MailboxItem[SessionCommandReceipt]
            | _InternalMailboxItem[object]
            | _LeaseMailboxItem
            | _ObservationMailboxItem
            | object
        ] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        persisted = store.get_session(record.session_id)
        if persisted is None:
            store.save_session(record)
        elif persisted != record:
            raise SessionRevisionConflictError(
                f"controller record for {record.session_id} differs from persisted state"
            )

    @property
    def session_id(self) -> UUID:
        return self._session_id

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def record(self) -> HarnessSessionRecord:
        record = self._store.get_session(self._session_id)
        if record is None:
            raise SessionNotFoundError(f"session {self._session_id} no longer exists")
        return record

    async def recover(self) -> None:
        await self._backend.recover(self.record)

    async def execute(
        self,
        command: SessionCommand,
        *,
        principal: PrincipalRef,
        expected_revision: int | None = None,
        authorization: AuthorizationProof | None = None,
    ) -> SessionCommandReceipt:
        """Enqueue a discriminated command; callers can never invoke backend I/O."""

        if self._closed:
            raise SessionControllerClosedError("session controller is closed")
        self._ensure_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[SessionCommandReceipt] = loop.create_future()
        await self._mailbox.put(
            _MailboxItem(command, principal, expected_revision, authorization, future)
        )
        return await future

    async def run_internal(
        self,
        operation_id: UUID,
        effect: Callable[[], Awaitable[ResultT]],
        *,
        principal: PrincipalRef,
        required_capability: InternalSessionCapability,
        expected_revision: int | None = None,
    ) -> ResultT:
        """Serialize an existing typed verified capability through this mailbox.

        The closed public ``SessionCommand`` union remains the transport
        contract. This adapter is only for in-process verified capabilities
        (model selection, structured decisions, usage collection) whose typed
        reducers predate that union. They still share the same ownership,
        revision, and writer-lease boundary as public commands.
        """

        if self._closed:
            raise SessionControllerClosedError("session controller is closed")
        self._ensure_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ResultT] = loop.create_future()
        await self._mailbox.put(
            _InternalMailboxItem(
                operation_id=operation_id,
                effect=effect,
                principal=principal,
                expected_revision=expected_revision,
                required_capability=required_capability,
                result=future,
            )
        )
        return await future

    async def acquire_writer_lease(
        self,
        request: AcquireWriterLease,
        *,
        holder: PrincipalRef,
        force_authorized: bool = False,
        revocation_reason: str | None = None,
        authorization: AuthorizationProof | None = None,
    ) -> WriterLeaseReply:
        if request.session_id != self._session_id:
            raise ValueError("writer lease request targets another session")
        return await self._enqueue_lease(
            "acquire",
            request,
            holder=holder,
            force_authorized=force_authorized,
            reason=revocation_reason,
            authorization=authorization,
        )

    async def renew_writer_lease(
        self,
        request: RenewWriterLease,
        *,
        holder: PrincipalRef,
    ) -> WriterLeaseReply:
        return await self._enqueue_lease("renew", request, holder=holder)

    async def release_writer_lease(
        self,
        request: ReleaseWriterLease,
        *,
        holder: PrincipalRef,
        reason: str = "released by holder",
    ) -> WriterLeaseReply:
        return await self._enqueue_lease("release", request, holder=holder, reason=reason)

    async def observe(
        self,
        status: SessionStatus,
        *,
        observed_at: datetime,
    ) -> HarnessSessionRecord:
        """Converge a parser observation through the mutation mailbox."""

        if status not in {
            SessionStatus.READY,
            SessionStatus.WORKING,
            SessionStatus.AWAITING_INPUT,
            SessionStatus.AWAITING_APPROVAL,
        }:
            raise ValueError(f"{status.value} is not an observable live status")
        if self._closed:
            raise SessionControllerClosedError("session controller is closed")
        self._ensure_worker()
        future: asyncio.Future[HarnessSessionRecord] = asyncio.get_running_loop().create_future()
        await self._mailbox.put(_ObservationMailboxItem(status, observed_at, future))
        return await future

    async def _enqueue_lease(
        self,
        operation: Literal["acquire", "renew", "release"],
        request: AcquireWriterLease | RenewWriterLease | ReleaseWriterLease,
        *,
        holder: PrincipalRef,
        force_authorized: bool = False,
        reason: str | None = None,
        authorization: AuthorizationProof | None = None,
    ) -> WriterLeaseReply:
        if self._closed:
            raise SessionControllerClosedError("session controller is closed")
        self._ensure_worker()
        future: asyncio.Future[WriterLeaseReply] = asyncio.get_running_loop().create_future()
        await self._mailbox.put(
            _LeaseMailboxItem(
                operation,
                request,
                holder,
                force_authorized,
                authorization,
                reason,
                future,
            )
        )
        return await future

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        worker = self._worker
        if worker is None or worker.done():
            return
        await self._mailbox.put(_STOP)
        await worker

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            if self._worker is not None:
                # asyncio.Queue binds to the loop on which an empty ``get``
                # waits. Tests and embedded callers may intentionally invoke a
                # controller across successive asyncio.run() loops.
                self._mailbox = asyncio.Queue()
            self._worker = asyncio.create_task(
                self._run_mailbox(),
                name=f"murder-session-controller-{self._session_id}",
            )

    async def _run_mailbox(self) -> None:  # noqa: PLR0912, PLR0915
        while True:
            queued = await self._mailbox.get()
            if queued is _STOP:
                return
            if isinstance(queued, _LeaseMailboxItem):
                if queued.result.cancelled():
                    continue
                try:
                    reply = self._execute_lease(queued)
                except BaseException as exc:
                    if not queued.result.done():
                        queued.result.set_exception(exc)
                else:
                    if not queued.result.done():
                        queued.result.set_result(reply)
                continue
            if isinstance(queued, _ObservationMailboxItem):
                if queued.result.cancelled():
                    continue
                try:
                    record = self._execute_observation(queued)
                except BaseException as exc:
                    if not queued.result.done():
                        queued.result.set_exception(exc)
                else:
                    if not queued.result.done():
                        queued.result.set_result(record)
                continue
            if isinstance(queued, _InternalMailboxItem):
                internal = cast(_InternalMailboxItem[object], queued)
                if internal.result.cancelled():
                    continue
                try:
                    value = await self._execute_internal(internal)
                except BaseException as exc:
                    if not internal.result.done():
                        internal.result.set_exception(exc)
                else:
                    if not internal.result.done():
                        internal.result.set_result(value)
                continue
            item = cast(_MailboxItem[SessionCommandReceipt], queued)
            if item.result.cancelled():
                continue
            try:
                receipt = await self._execute_one(item)
            except BaseException as exc:
                if not item.result.done():
                    item.result.set_exception(exc)
            else:
                if not item.result.done():
                    item.result.set_result(receipt)

    async def _execute_one(
        self,
        item: _MailboxItem[SessionCommandReceipt],
    ) -> SessionCommandReceipt:
        # These reads deliberately happen after dequeue and immediately before
        # backend dispatch. Queue-time validation would permit stale commands.
        record = self.record
        if item.expected_revision is not None and record.revision != item.expected_revision:
            raise SessionRevisionConflictError(
                f"session revision is {record.revision}, expected {item.expected_revision}"
            )
        self._validate_status(record)
        self._validate_capability(record, item.command)
        await self._validate_writer(record, item.command, item.principal)
        # Proof validation is deliberately the final guard before backend I/O.
        # Capability, revision, status, and lease checks above contribute the
        # exact current effect context; no await occurs after authorization and
        # before dispatch.
        if not await self._authorizer(
            item.command,
            record,
            item.principal,
            item.authorization,
        ):
            raise SessionAuthorizationError("session command was not authorized")

        if isinstance(item.command, TerminateSession):
            return await self._execute_termination(
                item.command,
                record,
                item.principal,
            )

        await self._dispatch(item.command)
        completed_at = datetime.now(timezone.utc)
        updated = record.model_copy(
            update={
                "status": _status_after(item.command, record.status),
                "revision": record.revision + 1,
                "last_observed_at": completed_at,
            }
        )
        self._store.save_session(updated, expected_revision=record.revision)
        return SessionCommandReceipt(
            operation_id=item.command.operation_id,
            session_id=record.session_id,
            revision=updated.revision,
            completed_at=completed_at,
        )

    async def _execute_termination(
        self,
        command: TerminateSession,
        record: HarnessSessionRecord,
        principal: PrincipalRef,
    ) -> SessionCommandReceipt:
        correlation = Correlation(correlation_id=command.operation_id)
        stopping_at = datetime.now(timezone.utc)
        stopping = record.model_copy(
            update={
                "status": SessionStatus.STOPPING,
                "revision": record.revision + 1,
                "last_observed_at": stopping_at,
            }
        )
        self._store.save_session(stopping, expected_revision=record.revision)
        try:
            await self._dispatch(command)
        except BaseException:
            failed_at = datetime.now(timezone.utc)
            failed = stopping.model_copy(
                update={
                    "status": SessionStatus.FAILED,
                    "revision": stopping.revision + 1,
                    "last_observed_at": failed_at,
                    "stopped_at": failed_at,
                }
            )
            self._store.save_terminal_session(
                failed,
                expected_revision=stopping.revision,
                reason=command.reason or "session termination failed",
                actor=principal,
                correlation=correlation,
            )
            raise
        completed_at = datetime.now(timezone.utc)
        stopped = stopping.model_copy(
            update={
                "status": SessionStatus.STOPPED,
                "revision": stopping.revision + 1,
                "last_observed_at": completed_at,
                "stopped_at": completed_at,
            }
        )
        self._store.save_terminal_session(
            stopped,
            expected_revision=stopping.revision,
            reason=command.reason or "session terminated",
            actor=principal,
            correlation=correlation,
        )
        return SessionCommandReceipt(
            operation_id=command.operation_id,
            session_id=record.session_id,
            revision=stopped.revision,
            completed_at=completed_at,
        )

    def _execute_lease(self, item: _LeaseMailboxItem) -> WriterLeaseReply:
        if item.operation == "acquire":
            assert isinstance(item.request, AcquireWriterLease)
            force_authorized = item.force_authorized
            current_lease = self._store.active_writer_lease(item.request.session_id)
            if (
                item.request.force
                and current_lease is not None
                and self._takeover_authorizer is not None
            ):
                force_authorized = self._takeover_authorizer(
                    item.request,
                    item.holder,
                    current_lease,
                    item.authorization,
                )
            return self._store.acquire_writer_lease(
                item.request,
                holder=item.holder,
                force_authorized=force_authorized,
                revocation_reason=item.reason,
            )
        if item.operation == "renew":
            assert isinstance(item.request, RenewWriterLease)
            return self._store.renew_writer_lease(item.request, holder=item.holder)
        assert isinstance(item.request, ReleaseWriterLease)
        return self._store.release_writer_lease(
            item.request,
            holder=item.holder,
            reason=item.reason or "released by holder",
        )

    def _execute_observation(self, item: _ObservationMailboxItem) -> HarnessSessionRecord:
        record = self.record
        self._validate_status(record)
        observed = record.model_copy(
            update={
                "status": item.status,
                "revision": record.revision + 1,
                "last_observed_at": item.observed_at,
            }
        )
        self._store.save_session(observed, expected_revision=record.revision)
        return observed

    async def _execute_internal(self, item: _InternalMailboxItem[ResultT]) -> ResultT:
        record = self.record
        if item.expected_revision is not None and record.revision != item.expected_revision:
            raise SessionRevisionConflictError(
                f"session revision is {record.revision}, expected {item.expected_revision}"
            )
        self._validate_status(record)
        if not getattr(record.capabilities, item.required_capability):
            raise SessionCapabilityError(
                f"{item.required_capability} is not supported by session {record.session_id}"
            )
        await self._validate_internal_writer(record, item.principal)
        value = await item.effect()
        completed_at = datetime.now(timezone.utc)
        updated = record.model_copy(
            update={
                "revision": record.revision + 1,
                "last_observed_at": completed_at,
            }
        )
        self._store.save_session(updated, expected_revision=record.revision)
        return value

    @staticmethod
    def _validate_status(record: HarnessSessionRecord) -> None:
        if record.status in {
            SessionStatus.STOPPING,
            SessionStatus.STOPPED,
            SessionStatus.FAILED,
            SessionStatus.LOST,
        }:
            raise SessionControllerError(
                f"session {record.session_id} cannot mutate while {record.status.value}"
            )

    @staticmethod
    def _validate_capability(
        record: HarnessSessionRecord,
        command: SessionCommand,
    ) -> None:
        caps = record.capabilities
        supported = True
        if isinstance(command, SendStructuredMessage):
            supported = caps.structured_messages
        elif isinstance(command, (WriteTerminalInput, ResizeTerminal)):
            supported = caps.raw_terminal
        elif isinstance(command, InterruptSession):
            supported = caps.interruptible
        if not supported:
            raise SessionCapabilityError(
                f"{command.type} is not supported by session {record.session_id}"
            )

    async def _validate_writer(
        self,
        record: HarnessSessionRecord,
        command: SessionCommand,
        principal: PrincipalRef,
    ) -> None:
        if isinstance(command, WriteTerminalInput):
            self._store.validate_writer_lease(
                session_id=record.session_id,
                lease_id=command.lease_id,
                fence=command.fence,
                holder=principal,
                required_mode=WriterMode.RAW_TERMINAL,
            )
            return
        if not isinstance(command, SendStructuredMessage):
            return
        active = self._store.active_writer_lease(record.session_id)
        if active is None:
            return
        if active.mode is WriterMode.STRUCTURED and active.holder == principal:
            return
        is_automation = principal.kind in {PrincipalKind.WORKFLOW, PrincipalKind.SERVICE}
        human_raw_owner = active.mode is WriterMode.RAW_TERMINAL and active.holder.kind in {
            PrincipalKind.USER,
            PrincipalKind.CLIENT,
        }
        if human_raw_owner and is_automation:
            cooperative = await self._cooperative_input_policy(
                record,
                active.holder,
                principal,
            )
            if not cooperative:
                raise WriterLeaseRequiredError(
                    "human raw-terminal lease blocks automated structured input"
                )
            return
        raise WriterLeaseRequiredError("session has an incompatible active writer lease")

    async def _validate_internal_writer(
        self,
        record: HarnessSessionRecord,
        principal: PrincipalRef,
    ) -> None:
        active = self._store.active_writer_lease(record.session_id)
        if active is None:
            return
        if active.mode is WriterMode.STRUCTURED and active.holder == principal:
            return
        is_automation = principal.kind in {PrincipalKind.WORKFLOW, PrincipalKind.SERVICE}
        human_raw_owner = active.mode is WriterMode.RAW_TERMINAL and active.holder.kind in {
            PrincipalKind.USER,
            PrincipalKind.CLIENT,
        }
        if human_raw_owner and is_automation:
            if await self._cooperative_input_policy(record, active.holder, principal):
                return
        raise WriterLeaseRequiredError("session has an incompatible active writer lease")

    async def _dispatch(self, command: SessionCommand) -> None:
        if isinstance(command, SendStructuredMessage):
            await self._backend.send_structured_message(command)
        elif isinstance(command, WriteTerminalInput):
            await self._backend.write_terminal_input(command, _terminal_bytes(command))
        elif isinstance(command, ResizeTerminal):
            await self._backend.resize_terminal(command)
        elif isinstance(command, InterruptSession):
            await self._backend.interrupt(command)
        elif isinstance(command, TerminateSession):
            await self._backend.terminate(command)
        else:  # pragma: no cover - the closed Pydantic union prevents this
            raise TypeError(f"unsupported session command {type(command).__name__}")


def _terminal_bytes(command: WriteTerminalInput) -> bytes:
    if command.encoding == "utf-8":
        return command.data.encode("utf-8")
    try:
        return base64.b64decode(command.data, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SessionControllerError("terminal input contains invalid base64") from exc


def _status_after(command: SessionCommand, current: SessionStatus) -> SessionStatus:
    if isinstance(command, SendStructuredMessage):
        return SessionStatus.WORKING
    if isinstance(command, TerminateSession):
        return SessionStatus.STOPPED
    return current


__all__ = [
    "SessionAuthorizationError",
    "SessionCapabilityError",
    "SessionCommandAuthorizer",
    "SessionController",
    "SessionControllerClosedError",
    "SessionControllerError",
    "StaleWriterLeaseError",
    "InternalSessionCapability",
    "trusted_local_session_authorizer",
]
