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
