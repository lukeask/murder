"""Contract tests for the single serialized harness actuator."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AppServerRpcEffect,
    DelayProfile,
    EmissionStatus,
    PasteBuffer,
    SendLiteralKeys,
    SendNamedKey,
    SleepEffect,
)
from murder.llm.harness_control.runtime.acp_transport import AcpEffectTransport
from murder.llm.harness_control.runtime.actuator import HarnessActuator, IntentPriority
from murder.llm.harness_control.runtime.app_server_transport import AppServerEffectTransport
from murder.llm.harness_control.runtime.tmux_transport import TmuxTerminalEffectTransport


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object | None]] = []
        self.literal_started = asyncio.Event()
        self.release_literal = asyncio.Event()
        self.block_literal = False
        self.fail_paste = False

    async def send_literal_keys(self, text: str, *, inter_key_delay: object | None) -> None:
        self.calls.append(("literal", text, inter_key_delay))
        self.literal_started.set()
        if self.block_literal:
            await self.release_literal.wait()

    async def paste_buffer(self, text: str) -> None:
        self.calls.append(("paste", text, None))
        if self.fail_paste:
            raise RuntimeError("paste rejected")

    async def send_named_key(self, key: str) -> None:
        self.calls.append(("key", key, None))

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None:
        self.calls.append(("app_server_rpc", effect, None))

    async def invoke_agent_sdk(self, effect: object) -> None:
        self.calls.append(("agent_sdk", effect, None))

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None:
        self.calls.append(("acp_rpc", effect, None))


class RecordingRpcPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object | None]] = []

    async def request(self, method: str, params: dict[str, object] | None = None) -> object:
        self.calls.append(("request", method, params))
        return {"ok": True}

    async def notify(self, method: str, params: dict[str, object] | None = None) -> None:
        self.calls.append(("notify", method, params))

    async def respond(
        self,
        id: str | int,
        *,
        result: dict[str, object] | None = None,
        error: dict[str, object] | None = None,
    ) -> None:
        self.calls.append(("respond", id, {"result": result, "error": error}))


def test_effects_are_serialized_and_waiters_are_priority_ordered() -> None:
    async def scenario() -> None:
        transport = RecordingTransport()
        transport.block_literal = True
        actuator = HarnessActuator(transport)

        low = asyncio.create_task(
            actuator.emit(
                "prompt",
                [SendLiteralKeys("first", "low-text")],
                priority=IntentPriority.PROMPT_SUBMISSION,
            )
        )
        await transport.literal_started.wait()
        medium = asyncio.create_task(
            actuator.emit(
                "model",
                [SendNamedKey("m", "Model")],
                priority=IntentPriority.MODEL_SELECTION,
            )
        )
        high = asyncio.create_task(
            actuator.emit(
                "interrupt",
                [SendNamedKey("i", "Escape")],
                priority=IntentPriority.USER_INTERRUPT,
            )
        )
        await asyncio.sleep(0)
        assert [call[1] for call in transport.calls] == ["low-text"]

        transport.release_literal.set()
        assert (await low).ok
        assert (await high).ok
        assert (await medium).ok
        assert [call[1] for call in transport.calls] == ["low-text", "Escape", "Model"]

    asyncio.run(scenario())


def test_transport_failure_is_recorded_and_prevents_later_effects() -> None:
    async def scenario() -> None:
        transport = RecordingTransport()
        transport.fail_paste = True
        result = await HarnessActuator(transport).emit(
            "submit",
            [PasteBuffer("paste", "payload"), SendNamedKey("enter", "Enter")],
        )
        assert not result.ok
        assert [item.status for item in result.results] == [
            EmissionStatus.FAILED,
            EmissionStatus.FAILED,
        ]
        assert "RuntimeError: paste rejected" == result.results[0].error
        assert result.results[1].error == result.results[0].error
        assert transport.calls == [("paste", "payload", None)]

    asyncio.run(scenario())


def test_literal_paste_named_key_and_sleep_use_distinct_transport_operations() -> None:
    async def scenario() -> None:
        transport = RecordingTransport()
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        delay = DelayProfile(1.0, 5.0)
        result = await HarnessActuator(transport, sleep=fake_sleep).emit(
            "submit",
            [
                SendLiteralKeys("literal", "typed", delay),
                PasteBuffer("paste", "pasted"),
                SendNamedKey("enter", "Enter"),
                SleepEffect("sleep", timedelta(milliseconds=125)),
            ],
        )
        assert result.ok
        assert transport.calls == [
            ("literal", "typed", delay),
            ("paste", "pasted", None),
            ("key", "Enter", None),
        ]
        assert sleeps == [0.125]

    asyncio.run(scenario())


def test_app_server_rpc_effect_is_dispatched_to_transport() -> None:
    async def scenario() -> None:
        transport = RecordingTransport()
        request = AppServerRpcEffect(
            "rpc:request",
            "thread/start",
            {"prompt": "hello"},
            expects_response=True,
        )
        notify = AppServerRpcEffect(
            "rpc:notify",
            "app/keepalive",
            None,
            expects_response=False,
        )
        reply = AppServerRpcEffect(
            "rpc:reply",
            "approval/respond",
            response_id="42",
            response_result={"decision": "allow"},
        )
        result = await HarnessActuator(transport).emit("app-server", [request, notify, reply])
        assert result.ok
        assert transport.calls == [
            ("app_server_rpc", request, None),
            ("app_server_rpc", notify, None),
            ("app_server_rpc", reply, None),
        ]

    asyncio.run(scenario())


def test_app_server_effect_transport_routes_request_notify_and_respond() -> None:
    async def scenario() -> None:
        port = RecordingRpcPort()
        transport = AppServerEffectTransport(port)
        actuator = HarnessActuator(transport)

        request = AppServerRpcEffect(
            "rpc:request",
            "thread/start",
            {"prompt": "hello"},
            expects_response=True,
        )
        notify = AppServerRpcEffect(
            "rpc:notify",
            "app/keepalive",
            None,
            expects_response=False,
        )
        reply = AppServerRpcEffect(
            "rpc:reply",
            "approval/respond",
            response_id=7,
            response_result={"decision": "allow"},
            response_error=None,
        )
        result = await actuator.emit("app-server", [request, notify, reply])
        assert result.ok
        assert port.calls == [
            ("request", "thread/start", {"prompt": "hello"}),
            ("notify", "app/keepalive", None),
            ("respond", 7, {"result": {"decision": "allow"}, "error": None}),
        ]

    asyncio.run(scenario())


def test_app_server_effect_transport_rejects_keystroke_effects() -> None:
    async def scenario() -> None:
        transport = AppServerEffectTransport(RecordingRpcPort())
        result = await HarnessActuator(transport).emit(
            "keys",
            [SendLiteralKeys("literal", "typed"), SendNamedKey("enter", "Enter")],
        )
        assert not result.ok
        assert result.results[0].status is EmissionStatus.FAILED
        assert "app-server transport does not accept keystroke effects" in (
            result.results[0].error or ""
        )
        assert result.results[1].error == result.results[0].error

    asyncio.run(scenario())


def test_tmux_transport_rejects_app_server_rpc() -> None:
    async def scenario() -> None:
        transport = TmuxTerminalEffectTransport("unused-session")
        with pytest.raises(TypeError, match="tmux transport cannot invoke app-server RPC"):
            await transport.invoke_app_server_rpc(
                AppServerRpcEffect("rpc", "thread/start", {"prompt": "x"})
            )

    asyncio.run(scenario())


def test_acp_rpc_effect_is_dispatched_to_transport() -> None:
    async def scenario() -> None:
        transport = RecordingTransport()
        request = AcpRpcEffect(
            "rpc:request",
            "session/prompt",
            {"sessionId": "s1", "prompt": [{"type": "text", "text": "hi"}]},
            expects_response=True,
        )
        notify = AcpRpcEffect(
            "rpc:notify",
            "session/cancel",
            {"sessionId": "s1"},
            expects_response=False,
        )
        reply = AcpRpcEffect(
            "rpc:reply",
            "permission/respond",
            response_id="42",
            response_result={"outcome": {"outcome": "selected", "optionId": "allow-once"}},
        )
        result = await HarnessActuator(transport).emit("acp", [request, notify, reply])
        assert result.ok
        assert transport.calls == [
            ("acp_rpc", request, None),
            ("acp_rpc", notify, None),
            ("acp_rpc", reply, None),
        ]

    asyncio.run(scenario())


def test_acp_effect_transport_routes_request_notify_and_respond() -> None:
    async def scenario() -> None:
        port = RecordingRpcPort()
        transport = AcpEffectTransport(port)
        actuator = HarnessActuator(transport)

        request = AcpRpcEffect(
            "rpc:request",
            "session/prompt",
            {"sessionId": "s1"},
            expects_response=True,
        )
        notify = AcpRpcEffect(
            "rpc:notify",
            "session/cancel",
            {"sessionId": "s1"},
            expects_response=False,
        )
        reply = AcpRpcEffect(
            "rpc:reply",
            "permission/respond",
            response_id=7,
            response_result={"outcome": {"outcome": "selected", "optionId": "allow-once"}},
            response_error=None,
        )
        result = await actuator.emit("acp", [request, notify, reply])
        assert result.ok
        assert port.calls == [
            ("request", "session/prompt", {"sessionId": "s1"}),
            ("notify", "session/cancel", {"sessionId": "s1"}),
            (
                "respond",
                7,
                {
                    "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}},
                    "error": None,
                },
            ),
        ]

    asyncio.run(scenario())


def test_acp_effect_transport_rejects_keystroke_effects() -> None:
    async def scenario() -> None:
        transport = AcpEffectTransport(RecordingRpcPort())
        result = await HarnessActuator(transport).emit(
            "keys",
            [SendLiteralKeys("literal", "typed"), SendNamedKey("enter", "Enter")],
        )
        assert not result.ok
        assert result.results[0].status is EmissionStatus.FAILED
        assert "ACP transport does not accept keystroke effects" in (result.results[0].error or "")
        assert result.results[1].error == result.results[0].error

    asyncio.run(scenario())


def test_transport_reject_matrix_cross_rpc() -> None:
    async def scenario() -> None:
        app_server = AppServerEffectTransport(RecordingRpcPort())
        acp = AcpEffectTransport(RecordingRpcPort())
        tmux = TmuxTerminalEffectTransport("unused-session")

        with pytest.raises(TypeError, match="app-server transport cannot invoke ACP RPC"):
            await app_server.invoke_acp_rpc(AcpRpcEffect("rpc", "session/cancel", {}))
        with pytest.raises(TypeError, match="ACP transport cannot invoke app-server RPC"):
            await acp.invoke_app_server_rpc(AppServerRpcEffect("rpc", "turn/start", {}))
        with pytest.raises(TypeError, match="tmux transport cannot invoke ACP RPC"):
            await tmux.invoke_acp_rpc(AcpRpcEffect("rpc", "session/cancel", {}))

    asyncio.run(scenario())
