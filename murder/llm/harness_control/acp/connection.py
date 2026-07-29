"""Stdio JSONL connection to an ACP agent process.

Owns the subprocess (or an injected transport), pending client-request futures,
and queues for agent notifications / agent→client requests. Connection-local
staged state (`session_id`, `staged_composer_text`, `desired_model`,
`desired_effort`, `prompt_in_flight`) is the shared surface later workstreams
extend for frame snapshots.

Constructor takes ``argv`` from an :class:`AcpAgentProfile` (or an injected
transport). No agent binary is hardcoded here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, Protocol

from murder.llm.harness_control.acp.protocol import (
    Params,
    RequestId,
    RpcError,
    RpcMessage,
    RpcNotification,
    RpcRequest,
    RpcResponse,
    decode_line,
    encode_message,
)

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT_S = 60.0


class AcpRpcError(RuntimeError):
    """Raised when the agent returns a JSON-RPC error response."""

    def __init__(self, error: RpcError, *, request_id: RequestId | None = None) -> None:
        self.error = error
        self.request_id = request_id
        detail = f"{error.code}: {error.message}"
        if request_id is not None:
            detail = f"id={request_id!r} {detail}"
        super().__init__(detail)


class AcpTransport(Protocol):
    """Minimal duplex line transport used by :class:`AcpConnection`."""

    async def write_line(self, line: str) -> None: ...

    async def readline(self) -> str:
        """Return the next line (without the trailing newline), or ``\"\"`` at EOF."""

    async def aclose(self) -> None: ...


class _ProcessTransport:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.stdout is None:
            raise ValueError("ACP process must expose stdin and stdout pipes")
        self._process = process
        self._stdin = process.stdin
        self._stdout = process.stdout

    async def write_line(self, line: str) -> None:
        self._stdin.write((line + "\n").encode("utf-8"))
        await self._stdin.drain()

    async def readline(self) -> str:
        raw = await self._stdout.readline()
        if not raw:
            return ""
        return raw.decode("utf-8").rstrip("\r\n")

    async def aclose(self) -> None:
        if self._stdin is not None and not self._stdin.is_closing():
            self._stdin.close()
            try:
                await self._stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()


class AcpConnection:
    """Bidirectional JSON-RPC session over JSONL stdio (or a test transport)."""

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        transport: AcpTransport | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        if transport is not None and argv is not None:
            raise ValueError("pass either transport or argv, not both")
        if transport is None and argv is None:
            raise ValueError("pass argv (from an AcpAgentProfile) or an injected transport")
        self._argv = tuple(argv) if argv is not None else ()
        self._injected_transport = transport
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._request_timeout_s = request_timeout_s

        self._transport: AcpTransport | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[RequestId, asyncio.Future[Any]] = {}
        self._next_id = 1
        self._started = False
        self._closed = False
        self._transport_exit: str | None = None
        self._lifecycle_done = asyncio.Event()

        self.notifications: asyncio.Queue[RpcNotification] = asyncio.Queue()
        self.incoming_requests: asyncio.Queue[RpcRequest] = asyncio.Queue()
        self.resolved_request_ids: asyncio.Queue[RequestId] = asyncio.Queue()

        # Connection-local staged state shared with observer / adapter.
        self.session_id: str | None = None
        self.staged_composer_text: str = ""
        self.desired_model: str | None = None
        self.desired_effort: str | None = None
        # Cursor slow/fast maps onto the ACP ``fast`` model_config option when
        # parameterizedModelPicker is advertised; None = leave agent default.
        self.desired_fast_enabled: bool | None = None
        self.prompt_in_flight: bool = False
        # Live ``configOptions`` from session/new or the latest set_config_option.
        self.session_config_options: list[dict[str, Any]] = []
        # Set by AcpEffectTransport when session/prompt returns (or cancel fires);
        # consumed by AcpFrameObserver on the next capture_frame.
        self.pending_stop_reason: str | None = None
        # Set immediately before a session/prompt RPC. Cursor ACP does not emit
        # user_message_chunk updates, so the observer consumes this exact client
        # prompt to establish a turn boundary and a transcript user item.
        self.pending_prompt_text: str | None = None
        # Full configOptions payload awaiting observer merge (session/new or
        # session/set_config_option). Independent active-model readback — never
        # invent model ids from desired_model alone.
        self.pending_config_options: list[dict[str, Any]] | None = None

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("AcpConnection is closed")
        if self._injected_transport is not None:
            self._transport = self._injected_transport
        else:
            process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
            )
            self._process = process
            self._transport = _ProcessTransport(process)
            self._stderr_task = asyncio.create_task(
                self._drain_stderr(process),
                name="acp-stderr-drainer",
            )
        self._reader_task = asyncio.create_task(self._read_loop(), name="acp-reader")
        self._started = True

    async def request(
        self,
        method: str,
        params: Params = None,
        *,
        timeout_s: float | None = None,
    ) -> Any:
        self._ensure_started()
        request_id = self._allocate_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send(RpcRequest(id=request_id, method=method, params=params))
            return await asyncio.wait_for(
                future,
                timeout=self._request_timeout_s if timeout_s is None else timeout_s,
            )
        except TimeoutError:
            future.cancel()
            raise
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Params = None) -> None:
        self._ensure_started()
        await self._send(RpcNotification(method=method, params=params))

    async def respond(
        self,
        request_id: RequestId,
        *,
        result: Any = None,
        error: RpcError | dict[str, Any] | None = None,
    ) -> None:
        """Answer an agent→client request using the original request id."""
        self._ensure_started()
        rpc_error: RpcError | None
        if error is None:
            rpc_error = None
        elif isinstance(error, RpcError):
            rpc_error = error
        else:
            code = error.get("code")
            message = error.get("message")
            if not isinstance(code, int) or isinstance(code, bool):
                raise ValueError("error.code must be an int")
            if not isinstance(message, str):
                raise ValueError("error.message must be a string")
            rpc_error = RpcError(code=code, message=message, data=error.get("data"))
        if rpc_error is not None:
            await self._send(RpcResponse(id=request_id, error=rpc_error))
        else:
            await self._send(RpcResponse(id=request_id, result=result))
            self.resolved_request_ids.put_nowait(request_id)

    def drain_notifications(self) -> list[RpcNotification]:
        drained: list[RpcNotification] = []
        while True:
            try:
                drained.append(self.notifications.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    def drain_incoming_requests(self) -> list[RpcRequest]:
        drained: list[RpcRequest] = []
        while True:
            try:
                drained.append(self.incoming_requests.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    def drain_resolved_request_ids(self) -> list[RequestId]:
        """Return agent request ids successfully answered by this client."""

        drained: list[RequestId] = []
        while True:
            try:
                drained.append(self.resolved_request_ids.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    async def iter_notifications(self) -> AsyncIterator[RpcNotification]:
        while True:
            if not self.started and self.notifications.empty():
                return
            get_task = asyncio.create_task(self.notifications.get())
            done_task = asyncio.create_task(self._lifecycle_done.wait())
            try:
                finished, _pending_tasks = await asyncio.wait(
                    (get_task, done_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (get_task, done_task):
                    if not task.done():
                        task.cancel()
                for task in (get_task, done_task):
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            if get_task in finished and not get_task.cancelled():
                yield get_task.result()
                continue
            while True:
                try:
                    yield self.notifications.get_nowait()
                except asyncio.QueueEmpty:
                    return

    async def close(self) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        self._lifecycle_done.set()

        reader = self._reader_task
        self._reader_task = None

        transport = self._transport
        self._transport = None
        try:
            if transport is not None:
                try:
                    await transport.aclose()
                except Exception:  # noqa: BLE001 — shutdown must continue
                    logger.debug("ACP transport close failed", exc_info=True)
        finally:
            # Keep reading stdout while process shutdown waits so a noisy child
            # cannot fill that pipe and deadlock its own termination.
            if reader is not None:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass

        stderr_task = self._stderr_task
        self._stderr_task = None
        if stderr_task is not None:
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — stderr must not break shutdown
                logger.debug("ACP stderr drainer exited with error", exc_info=True)

        self._fail_pending(ConnectionError("ACP connection closed"))

    async def __aenter__(self) -> AcpConnection:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("AcpConnection is closed")
        if self._transport_exit is not None:
            raise ConnectionError(self._transport_exit)
        if not self._started or self._transport is None:
            raise RuntimeError("AcpConnection is not started")

    def _fail_pending(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    def _stdout_closed_error(self) -> ConnectionError:
        message = "ACP stdout closed"
        process = self._process
        if process is not None and process.returncode is not None:
            message = f"{message} (process returncode={process.returncode})"
        return ConnectionError(message)

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    async def _send(self, message: RpcMessage) -> None:
        assert self._transport is not None
        payload = encode_message(message)
        async with self._write_lock:
            await self._transport.write_line(payload)

    async def _read_loop(self) -> None:
        assert self._transport is not None
        try:
            while True:
                line = await self._transport.readline()
                if line == "":
                    break
                try:
                    message = decode_line(line)
                except Exception:  # noqa: BLE001
                    logger.warning("failed to decode ACP line: %r", line, exc_info=True)
                    continue
                self._route_message(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("ACP reader exited with error", exc_info=True)
        finally:
            exit_error = self._stdout_closed_error()
            self._fail_pending(exit_error)
            self._started = False
            if self._reader_task is asyncio.current_task():
                self._reader_task = None
            if not self._closed and self._transport_exit is None:
                self._transport_exit = f"AcpConnection transport exited: {exit_error}"
            self._lifecycle_done.set()

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        """Continuously discard child stderr so it cannot block the RPC process."""
        assert process.stderr is not None
        while await process.stderr.read(64 * 1024):
            pass

    def _route_message(self, message: RpcMessage) -> None:
        if isinstance(message, RpcResponse):
            future = self._pending.get(message.id)
            if future is None or future.done():
                logger.debug("dropping unmatched ACP response id=%r", message.id)
                return
            if message.error is not None:
                future.set_exception(AcpRpcError(message.error, request_id=message.id))
            else:
                future.set_result(message.result)
            return

        if isinstance(message, RpcRequest):
            self.incoming_requests.put_nowait(message)
            return

        if isinstance(message, RpcNotification):
            self.notifications.put_nowait(message)
            return
