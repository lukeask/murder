"""Serialized, transport-neutral emission of terminal effects.

Controllers select semantic actions and adapters lower them into
``TerminalEffect`` values.  This module is the only runtime boundary allowed
to execute those values against a harness session.  A transport reports only
whether it accepted an effect; later observations establish whether the
harness interpreted it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum
from typing import Protocol

from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AgentSdkEffect,
    AppServerRpcEffect,
    DelayProfile,
    EffectEmission,
    EmissionBatchResult,
    EmissionStatus,
    OperationId,
    PasteBuffer,
    SendLiteralKeys,
    SendNamedKey,
    SleepEffect,
    TerminalEffect,
)


class IntentPriority(IntEnum):
    """Arbitration priority for whole semantic operations."""

    BACKGROUND_USAGE = 10
    MODEL_SELECTION = 20
    PROMPT_SUBMISSION = 30
    PERMISSION_RESPONSE = 40
    USER_INTERRUPT = 100


class TerminalEffectTransport(Protocol):
    """Physical terminal transport used by one actuator/session.

    Production transports may be tmux keystroke emission or app-server JSON-RPC.
    Keeping this narrow prevents the actuator from importing transport-specific
    behavior.  ``inter_key_delay`` is intentionally passed through so a tmux
    transport can implement harness-approved fast humanized typing without
    exposing that physical policy to controllers.
    """

    async def send_literal_keys(
        self,
        text: str,
        *,
        inter_key_delay: DelayProfile | None,
    ) -> None: ...

    async def paste_buffer(self, text: str) -> None: ...

    async def send_named_key(self, key: str) -> None: ...

    async def invoke_app_server_rpc(self, effect: AppServerRpcEffect) -> None: ...

    async def invoke_agent_sdk(self, effect: AgentSdkEffect) -> None: ...

    async def invoke_acp_rpc(self, effect: AcpRpcEffect) -> None: ...


class ActuatorError(RuntimeError):
    """Base class for actuator arbitration errors."""


class OperationAlreadyEmittingError(ActuatorError):
    """The caller tried to concurrently emit two batches for one operation."""


@dataclass(slots=True)
class _EmissionRequest:
    operation_id: OperationId
    effects: tuple[TerminalEffect, ...]
    priority: IntentPriority
    sequence: int


@dataclass(frozen=True, slots=True)
class ActuatorState:
    """Inspectable arbitration state for runtime diagnostics and tests."""

    owner: OperationId | None
    owner_priority: IntentPriority | None
    pending_operations: tuple[OperationId, ...]


class HarnessActuator:
    """The sole serialized terminal-effect emitter for one harness session.

    A batch is exclusive while it is being emitted. Whole-operation preemption
    belongs to the durable session arbiter; the actuator has exactly one job:
    serialize already-selected effect batches.
    """

    def __init__(
        self,
        transport: TerminalEffectTransport,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._condition = asyncio.Condition()
        self._active: _EmissionRequest | None = None
        self._pending: list[_EmissionRequest] = []
        self._owned_operation_ids: set[OperationId] = set()
        self._next_sequence = 0

    async def state(self) -> ActuatorState:
        """Return a consistent diagnostic snapshot without exposing mutability."""

        async with self._condition:
            return ActuatorState(
                owner=self._active.operation_id if self._active else None,
                owner_priority=self._active.priority if self._active else None,
                pending_operations=tuple(request.operation_id for request in self._pending),
            )

    async def emit(
        self,
        operation_id: OperationId,
        effects: Sequence[TerminalEffect],
        *,
        priority: IntentPriority = IntentPriority.PROMPT_SUBMISSION,
    ) -> EmissionBatchResult:
        """Emit one lowered batch, exclusively, and return transport outcomes.

        Priority orders waiting work but never creates a second preemption
        authority below the semantic-operation arbiter.
        """

        request = await self._enqueue(
            operation_id,
            tuple(effects),
            priority=priority,
        )
        try:
            return await self._emit_owned(request)
        finally:
            await self._release(request)

    async def _enqueue(
        self,
        operation_id: OperationId,
        effects: tuple[TerminalEffect, ...],
        *,
        priority: IntentPriority,
    ) -> _EmissionRequest:
        async with self._condition:
            if operation_id in self._owned_operation_ids:
                raise OperationAlreadyEmittingError(
                    f"operation {operation_id!r} already owns or awaits this actuator"
                )

            request = _EmissionRequest(
                operation_id=operation_id,
                effects=effects,
                priority=priority,
                sequence=self._next_sequence,
            )
            self._next_sequence += 1
            self._owned_operation_ids.add(operation_id)
            self._pending.append(request)
            self._promote_next_if_idle()
            self._condition.notify_all()
            try:
                while self._active is not request:
                    await self._condition.wait()
                return request
            except BaseException:
                # A cancelled waiter never emitted an effect, so immediately
                # relinquish its reservation rather than leaving the session
                # permanently owned by a vanished operation task.
                if self._active is request:
                    self._active = None
                elif request in self._pending:
                    self._pending.remove(request)
                self._owned_operation_ids.discard(operation_id)
                self._promote_next_if_idle()
                self._condition.notify_all()
                raise

    def _promote_next_if_idle(self) -> None:
        if self._active is not None or not self._pending:
            return
        # Higher priority wins; order is stable for equal-priority requests.
        next_request = min(self._pending, key=lambda item: (-int(item.priority), item.sequence))
        self._pending.remove(next_request)
        self._active = next_request

    async def _emit_owned(self, request: _EmissionRequest) -> EmissionBatchResult:
        results: list[EffectEmission] = []
        stop_reason: str | None = None

        for effect in request.effects:
            if stop_reason is not None:
                results.append(
                    EffectEmission(effect.effect_id, EmissionStatus.FAILED, error=stop_reason)
                )
                continue

            try:
                await self._emit_effect(effect)
            except Exception as exc:  # transport failures are data, never a semantic success
                stop_reason = f"{type(exc).__name__}: {exc}"
                results.append(
                    EffectEmission(effect.effect_id, EmissionStatus.FAILED, error=stop_reason)
                )
            else:
                results.append(EffectEmission(effect.effect_id, EmissionStatus.EMITTED))

        return EmissionBatchResult(operation_id=request.operation_id, results=tuple(results))

    async def _emit_effect(self, effect: TerminalEffect) -> None:
        match effect:
            case SendLiteralKeys(text=text, inter_key_delay=delay):
                await self._transport.send_literal_keys(text, inter_key_delay=delay)
            case PasteBuffer(text=text):
                await self._transport.paste_buffer(text)
            case SendNamedKey(key=key):
                await self._transport.send_named_key(key)
            case SleepEffect(duration=duration):
                await self._sleep(_seconds(duration))
            case AppServerRpcEffect():
                await self._transport.invoke_app_server_rpc(effect)
            case AgentSdkEffect():
                await self._transport.invoke_agent_sdk(effect)
            case AcpRpcEffect():
                await self._transport.invoke_acp_rpc(effect)
            case _:
                raise TypeError(f"unsupported terminal effect: {type(effect).__name__}")

    async def _release(self, request: _EmissionRequest) -> None:
        async with self._condition:
            if self._active is request:
                self._active = None
            # This should not happen for a promoted request, but makes a
            # cancellation while queued leave no ownership leak.
            elif request in self._pending:
                self._pending.remove(request)
            self._owned_operation_ids.discard(request.operation_id)
            self._promote_next_if_idle()
            self._condition.notify_all()


def _seconds(duration: timedelta) -> float:
    seconds = duration.total_seconds()
    if seconds < 0:
        raise ValueError("SleepEffect duration must not be negative")
    return seconds


__all__ = [
    "ActuatorError",
    "ActuatorState",
    "HarnessActuator",
    "IntentPriority",
    "OperationAlreadyEmittingError",
    "TerminalEffectTransport",
]
