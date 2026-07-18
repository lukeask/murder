"""Transport-neutral effect boundary for one live harness session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from murder.llm.harness_control.model.actions import InputChunk, InputProvenance
from murder.llm.harness_control.model.operations import OperationOutcome
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.runtime.sessions.contracts import (
    HarnessSessionRecord,
    InterruptSession,
    ResizeTerminal,
    SendStructuredMessage,
    TerminateSession,
    WriteTerminalInput,
)
from murder.runtime.terminal import tmux


class SessionBackend(Protocol):
    """Distinct transports implementing the same controller-owned mutations."""

    async def recover(self, record: HarnessSessionRecord) -> None: ...

    async def send_structured_message(self, command: SendStructuredMessage) -> None: ...

    async def write_terminal_input(
        self,
        command: WriteTerminalInput,
        data: bytes,
    ) -> None: ...

    async def resize_terminal(self, command: ResizeTerminal) -> None: ...

    async def interrupt(self, command: InterruptSession) -> None: ...

    async def terminate(self, command: TerminateSession) -> None: ...


class AppServerClient(Protocol):
    """Structured app-server seam; no vendor wire format leaks into controllers."""

    async def send_message(
        self,
        *,
        operation_id: str,
        text: str,
        activity_id: str | None,
    ) -> None: ...

    async def write_terminal(self, data: bytes) -> None: ...

    async def resize_terminal(self, *, columns: int, rows: int) -> None: ...

    async def interrupt(self, *, reason: str | None) -> None: ...

    async def terminate(self, *, force: bool, reason: str | None) -> None: ...

    async def recover(self, record: HarnessSessionRecord) -> None: ...


class AppServerSessionBackend:
    """Backend for a structured harness application server."""

    def __init__(self, client: AppServerClient) -> None:
        self._client = client

    async def recover(self, record: HarnessSessionRecord) -> None:
        await self._client.recover(record)

    async def send_structured_message(self, command: SendStructuredMessage) -> None:
        await self._client.send_message(
            operation_id=str(command.operation_id),
            text=command.text,
            activity_id=str(command.activity_id) if command.activity_id else None,
        )

    async def write_terminal_input(
        self,
        command: WriteTerminalInput,
        data: bytes,
    ) -> None:
        del command
        await self._client.write_terminal(data)

    async def resize_terminal(self, command: ResizeTerminal) -> None:
        await self._client.resize_terminal(columns=command.columns, rows=command.rows)

    async def interrupt(self, command: InterruptSession) -> None:
        await self._client.interrupt(reason=command.reason)

    async def terminate(self, command: TerminateSession) -> None:
        await self._client.terminate(force=command.force, reason=command.reason)


class TmuxSessionBackend:
    """Terminal-only backend for sessions without a structured adapter."""

    def __init__(self, session: str) -> None:
        if not session:
            raise ValueError("tmux session reference must not be empty")
        self._session = session

    async def recover(self, record: HarnessSessionRecord) -> None:
        if record.transport_ref != self._session:
            raise SessionBackendError("tmux backend does not match the persisted transport")
        if not await tmux.session_exists(self._session):
            raise SessionBackendError(f"tmux session {self._session!r} no longer exists")

    async def send_structured_message(self, command: SendStructuredMessage) -> None:
        del command
        raise SessionBackendError("terminal-only tmux backend has no structured message adapter")

    async def write_terminal_input(
        self,
        command: WriteTerminalInput,
        data: bytes,
    ) -> None:
        del command
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionBackendError("terminal-only tmux backend accepts UTF-8 input") from exc
        await tmux.send_keys(self._session, text, literal=True, enter=False)

    async def resize_terminal(self, command: ResizeTerminal) -> None:
        await tmux.resize_session(
            self._session,
            columns=command.columns,
            rows=command.rows,
        )

    async def interrupt(self, command: InterruptSession) -> None:
        del command
        await tmux.send_keys(
            self._session,
            "C-c",
            literal=False,
            enter=False,
        )

    async def terminate(self, command: TerminateSession) -> None:
        del command
        await tmux.kill_session(self._session)


RawInputWriter = Callable[[bytes], Awaitable[None]]
ResizeWriter = Callable[[int, int], Awaitable[None]]
TerminateWriter = Callable[[bool, str | None], Awaitable[None]]


class VerifiedHarnessSessionBackend:
    """Adapter retaining the existing verified runtime behind SessionController."""

    def __init__(
        self,
        control: VerifiedHarnessControlSession,
        *,
        raw_input_writer: RawInputWriter | None = None,
        resize_writer: ResizeWriter | None = None,
        terminate_writer: TerminateWriter | None = None,
    ) -> None:
        self._control = control
        self._raw_input_writer = raw_input_writer or self._write_tmux
        self._resize_writer = resize_writer or self._resize_tmux
        self._terminate_writer = terminate_writer or self._terminate_tmux

    async def recover(self, record: HarnessSessionRecord) -> None:
        del record
        await self._control.recover_pending_operations()

    async def send_structured_message(self, command: SendStructuredMessage) -> None:
        result = await self._control.submit_prompt(
            (
                InputChunk(
                    command.text,
                    provenance=InputProvenance.USER_PASTE_BLOCK,
                    stable_chunk_id=str(command.operation_id),
                ),
            )
        )
        if result.outcome not in {OperationOutcome.SUBMITTED, OperationOutcome.COMPLETED}:
            raise SessionBackendError(
                f"verified harness rejected operation {command.operation_id}: {result.outcome}",
                outcome=result.outcome,
            )

    async def write_terminal_input(
        self,
        command: WriteTerminalInput,
        data: bytes,
    ) -> None:
        pending_manual = self._control.pop_controller_manual_input(command.operation_id)
        if pending_manual is not None:
            from murder.llm.harness_control.runtime.manual_input import (  # noqa: PLC0415
                emit_manual_input,
            )

            text, literal, append_enter, source, action_id = pending_manual
            await emit_manual_input(
                self._control,
                text=text,
                literal=literal,
                append_enter=append_enter,
                source=source,
                _bypass_controller=True,
                _operation_id=str(command.operation_id),
                _action_id=action_id,
            )
            return
        await self._raw_input_writer(data)

    async def resize_terminal(self, command: ResizeTerminal) -> None:
        await self._resize_writer(command.columns, command.rows)

    async def interrupt(self, command: InterruptSession) -> None:
        del command
        if not await self._control.interrupt():
            raise SessionBackendError("verified harness did not acknowledge interruption")

    async def terminate(self, command: TerminateSession) -> None:
        await self._terminate_writer(command.force, command.reason)

    async def _write_tmux(self, data: bytes) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionBackendError(
                "tmux verified backend accepts UTF-8 terminal input only"
            ) from exc
        await tmux.send_keys(
            self._control.terminal_session,
            text,
            literal=True,
            enter=False,
        )

    async def _resize_tmux(self, columns: int, rows: int) -> None:
        await tmux.resize_session(self._control.terminal_session, columns=columns, rows=rows)

    async def _terminate_tmux(self, force: bool, reason: str | None) -> None:
        del force, reason
        await tmux.kill_session(self._control.terminal_session)


class SessionBackendError(RuntimeError):
    def __init__(self, message: str, *, outcome: object | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome


__all__ = [
    "AppServerClient",
    "AppServerSessionBackend",
    "SessionBackend",
    "SessionBackendError",
    "TmuxSessionBackend",
    "VerifiedHarnessSessionBackend",
]
