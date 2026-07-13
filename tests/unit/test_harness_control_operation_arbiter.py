from __future__ import annotations

import asyncio

import pytest

from murder.llm.harness_control.runtime.actuator import IntentPriority
from murder.llm.harness_control.runtime.operation_arbiter import (
    OperationPreemptedError,
    OperationPreemptionDeniedError,
    SessionOperationArbiter,
)


def test_session_operation_arbiter_enforces_exclusion_priority_and_explicit_interrupt(  # noqa: PLR0915
) -> None:
    async def scenario() -> None:  # noqa: PLR0915
        arbiter = SessionOperationArbiter()
        active = 0
        maximum_active = 0
        entered: list[str] = []
        background_entered = asyncio.Event()
        release_background = asyncio.Event()
        release_prompt = asyncio.Event()
        persisted_preemptions: list[tuple[str, str]] = []

        def preemption_hook(operation_id: str):
            async def persist(preempted_by: str) -> None:
                persisted_preemptions.append((operation_id, preempted_by))

            return persist

        async def operation(name: str, entered_event: asyncio.Event, release: asyncio.Event) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            entered.append(name)
            entered_event.set()
            try:
                await release.wait()
                return name
            finally:
                active -= 1

        background = asyncio.create_task(
            arbiter.run(
                "usage",
                IntentPriority.BACKGROUND_USAGE,
                lambda: operation("usage", background_entered, release_background),
                on_preempt=preemption_hook("usage"),
            )
        )
        await background_entered.wait()

        with pytest.raises(OperationPreemptionDeniedError):
            await arbiter.run(
                "model-preempt",
                IntentPriority.MODEL_SELECTION,
                lambda: operation("must-not-run", asyncio.Event(), asyncio.Event()),
                preempt_active=True,
            )

        model_entered = asyncio.Event()
        prompt_entered = asyncio.Event()
        model = asyncio.create_task(
            arbiter.run(
                "model",
                IntentPriority.MODEL_SELECTION,
                lambda: operation("model", model_entered, asyncio.Event()),
                on_preempt=preemption_hook("model"),
            )
        )
        prompt = asyncio.create_task(
            arbiter.run(
                "prompt",
                IntentPriority.PROMPT_SUBMISSION,
                lambda: operation("prompt", prompt_entered, release_prompt),
                on_preempt=preemption_hook("prompt"),
            )
        )
        await arbiter.wait_until_pending(frozenset({"model", "prompt"}))
        assert not model_entered.is_set() and not prompt_entered.is_set()

        release_background.set()
        assert await background == "usage"
        await prompt_entered.wait()
        assert entered == ["usage", "prompt"]
        assert not model_entered.is_set()

        interrupt_entered = asyncio.Event()
        interrupt_release = asyncio.Event()
        interrupt = asyncio.create_task(
            arbiter.run(
                "interrupt",
                IntentPriority.USER_INTERRUPT,
                lambda: operation("interrupt", interrupt_entered, interrupt_release),
                preempt_active=True,
            )
        )
        with pytest.raises(OperationPreemptedError, match="prompt.*interrupt"):
            await prompt
        await interrupt_entered.wait()
        assert persisted_preemptions == [("prompt", "interrupt")]
        assert entered == ["usage", "prompt", "interrupt"]
        assert not model_entered.is_set()

        interrupt_release.set()
        assert await interrupt == "interrupt"
        await model_entered.wait()
        model.cancel()
        with pytest.raises(asyncio.CancelledError):
            await model
        assert maximum_active == 1

    asyncio.run(scenario())
