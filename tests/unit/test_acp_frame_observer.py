"""Unit tests for AcpFrameObserver queue drain → frame JSON."""

from __future__ import annotations

import asyncio
import json

import pytest

from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.acp.protocol import RpcNotification, RpcRequest
from murder.llm.harness_control.model.evidence import HarnessId
from murder.llm.harness_control.runtime.acp_frame_observer import AcpFrameObserver


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
    connection = AcpConnection(transport=transport)
    await connection.start()
    connection.staged_composer_text = "draft"
    connection.desired_model = "gpt-5.4"
    connection.desired_effort = "medium"

    connection.notifications.put_nowait(
        RpcNotification(
            method="session/update",
            params={
                "sessionId": "sess-9",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hi"},
                },
            },
        )
    )
    connection.incoming_requests.put_nowait(
        RpcRequest(
            id="req-1",
            method="session/request_permission",
            params={"toolCall": {"title": "edit"}},
        )
    )

    observer = AcpFrameObserver(connection, HarnessId("cursor"))
    frame = await observer.capture_frame()
    payload = json.loads(frame.raw_text)

    assert payload["v"] == 1
    assert payload["session_id"] == "sess-9"
    assert payload["turn"] == {"status": "streaming"}
    assert payload["composer"] == {"text": "draft", "staged": True}
    assert payload["items"][0]["text"] == "hi"
    assert payload["pending_requests"][0]["id"] == "req-1"
    assert payload["model"] == {"id": "gpt-5.4", "effort": "medium"}
    assert connection.session_id == "sess-9"
    assert connection.notifications.empty()
    assert connection.incoming_requests.empty()

    # Idle re-capture must be hash-stable.
    again = await observer.capture_frame()
    assert again.raw_text == frame.raw_text
    assert again.capture_sequence == frame.capture_sequence + 1

    await connection.aclose()


@pytest.mark.asyncio
async def test_prompt_in_flight_marks_turn_streaming() -> None:
    transport = _IdleTransport()
    connection = AcpConnection(transport=transport)
    await connection.start()
    connection.session_id = "sess-1"
    connection.prompt_in_flight = True

    observer = AcpFrameObserver(connection, HarnessId("cursor"))
    frame = await observer.capture_frame()
    payload = json.loads(frame.raw_text)
    assert payload["turn"] == {"status": "streaming"}
    assert observer.view_state.turn_status == "streaming"

    await connection.aclose()
