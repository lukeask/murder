"""Smoke tests: VerifiedHarnessControlSession over a fake app-server connection."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.app_server.protocol import RpcNotification
from murder.llm.harness_control.model.actions import InputChunk, InputProvenance
from murder.llm.harness_control.model.operations import OperationOutcome
from murder.llm.harness_control.runtime.app_server_frame_observer import AppServerFrameObserver
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.state.persistence.schema import init_db


class AutoRespondTransport:
    """Scripted transport that auto-answers client requests and records them.

    Mirrors ``FakeTransport`` in ``test_app_server_protocol.py``, but resolves
    pending ``connection.request`` futures so actuators do not hang.
    """

    def __init__(self) -> None:
        self.written: list[str] = []
        self.requests: list[dict[str, object]] = []
        self._outbound: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    def push(self, line: str) -> None:
        self._outbound.put_nowait(line)

    def push_eof(self) -> None:
        self._outbound.put_nowait(None)

    def requests_named(self, method: str) -> list[dict[str, object]]:
        return [row for row in self.requests if row.get("method") == method]

    async def write_line(self, line: str) -> None:
        self.written.append(line)
        message = json.loads(line)
        if "method" in message and "id" in message:
            self.requests.append(message)
            method = message["method"]
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            result: dict[str, object] = {"ok": True}
            if method == "turn/start":
                thread_id = params.get("threadId") or "thread-1"
                inputs = params.get("input") if isinstance(params.get("input"), list) else []
                text = ""
                if inputs and isinstance(inputs[0], dict):
                    text = str(inputs[0].get("text") or "")
                result = {"turn": {"id": "turn-new", "status": "inProgress"}}
                self.push(
                    json.dumps(
                        {
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": "turn-new",
                                    "status": "inProgress",
                                    "items": [
                                        {
                                            "id": "u1",
                                            "type": "userMessage",
                                            "text": text,
                                        }
                                    ],
                                },
                            },
                        }
                    )
                )
            elif method == "turn/interrupt":
                thread_id = params.get("threadId") or "thread-1"
                turn_id = params.get("turnId") or "turn-9"
                self.push(
                    json.dumps(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {"id": turn_id, "status": "interrupted"},
                            },
                        }
                    )
                )
            self.push(json.dumps({"id": message["id"], "result": result}))

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
) -> tuple[VerifiedHarnessControlSession, AppServerConnection]:
    app_server = AppServerConnection(transport=transport, request_timeout_s=2.0)
    await app_server.start()
    app_server.thread_id = "thread-1"
    app_server.staged_composer_text = ""

    db = _db()
    session = VerifiedHarnessControlSession.from_app_server(
        app_server=app_server,
        harness_kind="codex",
        terminal_session="test",
        connection=db,
        persistence_session_id="app-server-smoke",
        prompt_policy=PromptDriverPolicy(
            observation_interval=timedelta(),
            maximum_observations=24,
        ),
        prompt_sleep=_no_sleep,
    )
    assert isinstance(session._observer, AppServerFrameObserver)
    return session, app_server


@pytest.mark.asyncio
async def test_submit_prompt_emits_turn_start_over_fake_app_server() -> None:
    transport = AutoRespondTransport()
    session, app_server = await _session(transport)
    try:
        result = await session.submit_prompt(
            (InputChunk("ping", InputProvenance.USER_PASTE_BLOCK, "chunk-1"),),
            submission_deadline=timedelta(seconds=5),
        )
        assert result.outcome is OperationOutcome.SUBMITTED
        starts = transport.requests_named("turn/start")
        assert len(starts) == 1
        assert starts[0]["params"] == {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "ping"}],
        }
        assert app_server.staged_composer_text == ""
    finally:
        await app_server.aclose()


@pytest.mark.asyncio
async def test_interrupt_emits_turn_interrupt_over_fake_app_server() -> None:
    transport = AutoRespondTransport()
    session, app_server = await _session(transport)
    try:
        # Seed a streaming turn so interrupt reconciliation sees active generation.
        app_server.notifications.put_nowait(
            RpcNotification(
                method="turn/started",
                params={
                    "threadId": "thread-1",
                    "turn": {"id": "turn-9", "status": "inProgress", "items": []},
                },
            )
        )
        ok = await session.interrupt(deadline=timedelta(seconds=5))
        assert ok is True
        interrupts = transport.requests_named("turn/interrupt")
        assert len(interrupts) == 1
        assert interrupts[0]["params"] == {
            "threadId": "thread-1",
            "turnId": "turn-9",
        }
        assert app_server.current_turn_id == "turn-9"
    finally:
        await app_server.aclose()
