"""Smoke tests: VerifiedHarnessControlSession over a fake ACP connection."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.acp.protocol import RpcNotification
from murder.llm.harness_control.model.actions import InputChunk, InputProvenance
from murder.llm.harness_control.model.operations import OperationOutcome
from murder.llm.harness_control.runtime.acp_frame_observer import AcpFrameObserver
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.state.persistence.schema import init_db


class AutoRespondTransport:
    """Scripted transport that auto-answers client requests and records them."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self.requests: list[dict[str, object]] = []
        self.notifications: list[dict[str, object]] = []
        self._outbound: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    def push(self, line: str) -> None:
        self._outbound.put_nowait(line)

    def push_eof(self) -> None:
        self._outbound.put_nowait(None)

    def requests_named(self, method: str) -> list[dict[str, object]]:
        return [row for row in self.requests if row.get("method") == method]

    def notifications_named(self, method: str) -> list[dict[str, object]]:
        return [row for row in self.notifications if row.get("method") == method]

    async def write_line(self, line: str) -> None:
        self.written.append(line)
        message = json.loads(line)
        if "method" in message and "id" in message:
            self.requests.append(message)
            method = message["method"]
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            result: dict[str, object] = {"ok": True}
            if method == "session/prompt":
                session_id = params.get("sessionId") or "sess-1"
                prompt = params.get("prompt") if isinstance(params.get("prompt"), list) else []
                text = ""
                if prompt and isinstance(prompt[0], dict):
                    text = str(prompt[0].get("text") or "")
                result = {"stopReason": "end_turn"}
                self.push(
                    json.dumps(
                        {
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "user_message_chunk",
                                    "content": {"type": "text", "text": text},
                                },
                            },
                        }
                    )
                )
                self.push(
                    json.dumps(
                        {
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": "pong"},
                                },
                            },
                        }
                    )
                )
            self.push(json.dumps({"id": message["id"], "result": result}))
        elif "method" in message:
            self.notifications.append(message)
            # Mirror app-server interrupt acknowledgment: after session/cancel,
            # the agent reports the turn idle with stopReason cancelled.
            if message.get("method") == "session/cancel":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                session_id = params.get("sessionId") or "sess-1"
                self.push(
                    json.dumps(
                        {
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "state_update",
                                    "sessionState": "idle",
                                    "stopReason": "cancelled",
                                },
                            },
                        }
                    )
                )

    async def readline(self) -> str:
        item = await self._outbound.get()
        if item is None:
            return ""
        return item

    async def aclose(self) -> None:
        self.closed = True
        self.push_eof()


async def _no_sleep(_: float) -> None:
    return None


def _db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    return connection


async def _session(
    transport: AutoRespondTransport,
) -> tuple[VerifiedHarnessControlSession, AcpConnection]:
    acp = AcpConnection(transport=transport, request_timeout_s=2.0)
    await acp.start()
    acp.session_id = "sess-1"
    acp.staged_composer_text = ""

    db = _db()
    session = VerifiedHarnessControlSession.from_acp(
        acp=acp,
        harness_kind="cursor",
        terminal_session="test",
        connection=db,
        persistence_session_id="acp-smoke",
        prompt_policy=PromptDriverPolicy(
            observation_interval=timedelta(),
            maximum_observations=24,
        ),
        prompt_sleep=_no_sleep,
    )
    assert isinstance(session._observer, AcpFrameObserver)
    assert session._acp_connection is acp
    return session, acp


@pytest.mark.asyncio
async def test_submit_prompt_emits_session_prompt_over_fake_acp() -> None:
    transport = AutoRespondTransport()
    session, acp = await _session(transport)
    try:
        result = await session.submit_prompt(
            (InputChunk("ping", InputProvenance.USER_PASTE_BLOCK, "chunk-1"),),
            submission_deadline=timedelta(seconds=5),
        )
        assert result.outcome is OperationOutcome.SUBMITTED
        prompts = transport.requests_named("session/prompt")
        assert len(prompts) == 1
        assert prompts[0]["params"] == {
            "sessionId": "sess-1",
            "prompt": [{"type": "text", "text": "ping"}],
        }
        assert acp.staged_composer_text == ""
    finally:
        await acp.aclose()


@pytest.mark.asyncio
async def test_interrupt_emits_session_cancel_over_fake_acp() -> None:
    transport = AutoRespondTransport()
    session, acp = await _session(transport)
    try:
        # Seed a streaming turn so interrupt reconciliation sees active generation.
        acp.notifications.put_nowait(
            RpcNotification(
                method="session/update",
                params={
                    "sessionId": "sess-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "working"},
                    },
                },
            )
        )
        ok = await session.interrupt(deadline=timedelta(seconds=5))
        assert ok is True
        cancels = transport.notifications_named("session/cancel")
        assert len(cancels) == 1
        assert cancels[0]["params"] == {"sessionId": "sess-1"}
    finally:
        await acp.aclose()
