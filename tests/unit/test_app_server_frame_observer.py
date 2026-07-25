"""Unit tests for AppServerFrameObserver queue drain → frame JSON."""

from __future__ import annotations

import asyncio
import json

import pytest

from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.app_server.protocol import RpcNotification, RpcRequest
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.runtime.app_server_frame_observer import AppServerFrameObserver


class _IdleTransport:
    def __init__(self) -> None:
        self._q: asyncio.Queue[str | None] = asyncio.Queue()
        self.written: list[str] = []

    async def write_line(self, line: str) -> None:
        self.written.append(line)

    async def readline(self) -> str:
        item = await self._q.get()
        return "" if item is None else item

    async def aclose(self) -> None:
        self._q.put_nowait(None)


@pytest.mark.asyncio
async def test_capture_frame_drains_queues_and_emits_stable_json() -> None:
    transport = _IdleTransport()
    connection = AppServerConnection(transport=transport)
    await connection.start()
    connection.staged_composer_text = "draft"
    connection.desired_model = "gpt-5.4"
    connection.desired_effort = "medium"

    connection.notifications.put_nowait(
        RpcNotification(
            method="thread/started",
            params={"thread": {"id": "th-9"}},
        )
    )
    connection.notifications.put_nowait(
        RpcNotification(
            method="turn/started",
            params={
                "threadId": "th-9",
                "turn": {"id": "tu-9", "status": "inProgress", "items": []},
            },
        )
    )
    connection.notifications.put_nowait(
        RpcNotification(
            method="item/agentMessage/delta",
            params={
                "threadId": "th-9",
                "turnId": "tu-9",
                "itemId": "m1",
                "delta": "hi",
            },
        )
    )
    connection.incoming_requests.put_nowait(
        RpcRequest(id="req-1", method="item/fileChange/requestApproval", params={"path": "a.py"})
    )

    observer = AppServerFrameObserver(connection, HarnessId("codex"))
    frame = await observer.capture_frame()
    payload = json.loads(frame.raw_text)

    assert payload["v"] == 1
    assert payload["thread_id"] == "th-9"
    assert payload["turn"] == {"id": "tu-9", "status": "streaming"}
    assert payload["composer"] == {"text": "draft", "staged": True}
    assert payload["items"][0]["text"] == "hi"
    assert payload["pending_requests"][0]["id"] == "req-1"
    assert payload["model"] == {"id": "gpt-5.4", "effort": "medium"}
    assert connection.thread_id == "th-9"
    assert connection.current_turn_id == "tu-9"
    assert connection.notifications.empty()
    assert connection.incoming_requests.empty()

    # Idle re-capture must be hash-stable.
    again = await observer.capture_frame()
    assert again.raw_text == frame.raw_text
    assert again.capture_sequence == frame.capture_sequence + 1

    await connection.aclose()


@pytest.mark.asyncio
async def test_successful_response_removes_request_from_next_observation() -> None:
    transport = _IdleTransport()
    connection = AppServerConnection(transport=transport)
    await connection.start()
    connection.incoming_requests.put_nowait(
        RpcRequest(
            id="permission-1",
            method="item/commandExecution/requestApproval",
            params={"command": "git status"},
        )
    )
    observer = AppServerFrameObserver(connection, HarnessId("codex"))

    first = json.loads((await observer.capture_frame()).raw_text)
    assert [request["id"] for request in first["pending_requests"]] == ["permission-1"]

    await connection.respond("permission-1", result={"decision": "accept"})

    second = json.loads((await observer.capture_frame()).raw_text)
    assert second["pending_requests"] == []

    await connection.aclose()


@pytest.mark.asyncio
async def test_failed_response_write_leaves_request_pending_and_retryable() -> None:
    transport = _IdleTransport()
    connection = AppServerConnection(transport=transport)
    await connection.start()
    connection.incoming_requests.put_nowait(
        RpcRequest(
            id="permission-1",
            method="item/commandExecution/requestApproval",
            params={"command": "git status"},
        )
    )
    observer = AppServerFrameObserver(connection, HarnessId("codex"))
    assert [request["id"] for request in json.loads((await observer.capture_frame()).raw_text)[
        "pending_requests"
    ]] == ["permission-1"]

    async def _fail_write(line: str) -> None:
        del line
        raise OSError("broken pipe")

    transport.write_line = _fail_write  # type: ignore[method-assign]
    with pytest.raises(OSError, match="broken pipe"):
        await connection.respond("permission-1", result={"decision": "accept"})

    still_pending = json.loads((await observer.capture_frame()).raw_text)
    assert [request["id"] for request in still_pending["pending_requests"]] == ["permission-1"]

    written: list[str] = []

    async def _ok_write(line: str) -> None:
        written.append(line)

    transport.write_line = _ok_write  # type: ignore[method-assign]
    await connection.respond("permission-1", result={"decision": "accept"})
    assert written
    assert json.loads((await observer.capture_frame()).raw_text)["pending_requests"] == []

    await connection.aclose()


@pytest.mark.asyncio
async def test_usage_limit_error_lands_in_frame_snapshot() -> None:
    transport = _IdleTransport()
    connection = AppServerConnection(transport=transport)
    await connection.start()

    limit_message = "You've hit your usage limit. Try again later."
    connection.notifications.put_nowait(
        RpcNotification(
            method="turn/started",
            params={
                "threadId": "th-err",
                "turn": {"id": "tu-err", "status": "inProgress", "items": []},
            },
        )
    )
    connection.notifications.put_nowait(
        RpcNotification(
            method="error",
            params={
                "threadId": "th-err",
                "turnId": "tu-err",
                "willRetry": False,
                "error": {
                    "message": limit_message,
                    "codexErrorInfo": "usageLimitExceeded",
                },
            },
        )
    )
    connection.notifications.put_nowait(
        RpcNotification(
            method="turn/completed",
            params={
                "threadId": "th-err",
                "turn": {
                    "id": "tu-err",
                    "status": "failed",
                    "items": [{"id": "u1", "type": "userMessage", "text": "ping"}],
                    "error": {
                        "message": limit_message,
                        "codexErrorInfo": "usageLimitExceeded",
                    },
                },
            },
        )
    )

    observer = AppServerFrameObserver(connection, HarnessId("codex"))
    payload = json.loads((await observer.capture_frame()).raw_text)
    assert payload["turn"]["status"] == "failed"
    assert payload["turn"]["error"]["codexErrorInfo"] == "usageLimitExceeded"
    assert payload["turn"]["error"]["message"] == limit_message
    assert payload["items"][0]["text"] == "ping"

    await connection.aclose()
