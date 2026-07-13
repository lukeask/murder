"""Contract tests for the single serialized harness actuator."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from murder.llm.harness_control.model.actions import (
    DelayProfile,
    EmissionStatus,
    PasteBuffer,
    SendLiteralKeys,
    SendNamedKey,
    SleepEffect,
)
from murder.llm.harness_control.runtime.actuator import HarnessActuator, IntentPriority


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object | None]] = []
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
