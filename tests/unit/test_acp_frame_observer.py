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


@pytest.mark.asyncio
async def test_pending_stop_reason_clears_streaming_after_prompt() -> None:
    """session/prompt completion must leave awaiting_input, not stuck working."""
    transport = _IdleTransport()
    connection = AcpConnection(transport=transport)
    await connection.start()
    connection.session_id = "sess-1"
    connection.prompt_in_flight = True

    observer = AcpFrameObserver(connection, HarnessId("cursor"))
    streaming = json.loads((await observer.capture_frame()).raw_text)
    assert streaming["turn"] == {"status": "streaming"}

    # Transport clears prompt_in_flight and stashes stopReason for the next frame.
    connection.prompt_in_flight = False
    connection.pending_stop_reason = "end_turn"

    completed = json.loads((await observer.capture_frame()).raw_text)
    assert completed["turn"] == {"status": "completed"}
    assert completed["stop_reason"] == "end_turn"
    assert connection.pending_stop_reason is None
    assert observer.view_state.turn_status == "completed"

    await connection.aclose()


@pytest.mark.asyncio
async def test_consecutive_cursor_prompts_keep_user_and_reply_turn_boundaries() -> None:
    """Cursor omits user chunks; submitted prompts must still split ACP streams."""
    transport = _IdleTransport()
    connection = AcpConnection(transport=transport)
    await connection.start()
    observer = AcpFrameObserver(connection, HarnessId("cursor"))

    def queue_turn(prompt: str, thought: str, reply: str) -> None:
        connection.pending_prompt_text = prompt
        for kind, text in (
            ("agent_thought_chunk", thought),
            ("agent_message_chunk", reply),
        ):
            connection.notifications.put_nowait(
                RpcNotification(
                    method="session/update",
                    params={
                        "sessionId": "sess-1",
                        "update": {
                            "sessionUpdate": kind,
                            "content": {"type": "text", "text": text},
                        },
                    },
                )
            )
        connection.pending_stop_reason = "end_turn"

    queue_turn("test", "Preparing first.", "Here — ready.")
    await observer.capture_frame()
    queue_turn(
        "Test part 2, please use bullets",
        "Preparing bullets.",
        "- One\n- Two",
    )
    payload = json.loads((await observer.capture_frame()).raw_text)

    assert [(item["type"], item["text"]) for item in payload["items"]] == [
        ("userMessage", "test"),
        ("agentThought", "Preparing first."),
        ("agentMessage", "Here — ready."),
        ("userMessage", "Test part 2, please use bullets"),
        ("agentThought", "Preparing bullets."),
        ("agentMessage", "- One\n- Two"),
    ]

    await connection.aclose()


@pytest.mark.asyncio
async def test_successful_response_removes_request_from_next_observation() -> None:
    transport = _IdleTransport()
    connection = AcpConnection(transport=transport)
    await connection.start()
    connection.incoming_requests.put_nowait(
        RpcRequest(
            id="question-1",
            method="cursor/ask_question",
            params={"questions": [{"question": "Continue?"}]},
        )
    )
    observer = AcpFrameObserver(connection, HarnessId("cursor"))

    first = json.loads((await observer.capture_frame()).raw_text)
    assert [request["id"] for request in first["pending_requests"]] == ["question-1"]

    await connection.respond("question-1", result={"answers": ["Yes"]})

    second = json.loads((await observer.capture_frame()).raw_text)
    assert second["pending_requests"] == []

    await connection.aclose()


@pytest.mark.asyncio
async def test_failed_response_write_leaves_request_pending_and_retryable() -> None:
    transport = _IdleTransport()
    connection = AcpConnection(transport=transport)
    await connection.start()
    connection.incoming_requests.put_nowait(
        RpcRequest(
            id="question-1",
            method="cursor/ask_question",
            params={"questions": [{"question": "Continue?"}]},
        )
    )
    observer = AcpFrameObserver(connection, HarnessId("cursor"))
    assert [
        request["id"]
        for request in json.loads((await observer.capture_frame()).raw_text)["pending_requests"]
    ] == ["question-1"]

    async def _fail_write(line: str) -> None:
        del line
        raise OSError("broken pipe")

    transport.write_line = _fail_write  # type: ignore[method-assign]
    with pytest.raises(OSError, match="broken pipe"):
        await connection.respond("question-1", result={"answers": ["Yes"]})

    still_pending = json.loads((await observer.capture_frame()).raw_text)
    assert [request["id"] for request in still_pending["pending_requests"]] == ["question-1"]

    written: list[str] = []

    async def _ok_write(line: str) -> None:
        written.append(line)

    transport.write_line = _ok_write  # type: ignore[method-assign]
    await connection.respond("question-1", result={"answers": ["Yes"]})
    assert written
    assert json.loads((await observer.capture_frame()).raw_text)["pending_requests"] == []

    await connection.aclose()
