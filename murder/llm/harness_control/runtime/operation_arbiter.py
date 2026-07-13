"""Session-wide arbitration for complete semantic operation loops.

The actuator serializes individual effect batches.  This arbiter sits above it
and owns the larger observation -> decision -> action -> verification lease, so
two capabilities cannot interpret and mutate one pane from interleaved views.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from murder.llm.harness_control.runtime.actuator import IntentPriority

T = TypeVar("T")


class OperationArbitrationError(RuntimeError):
    """Base class for semantic-operation scheduling failures."""


class OperationPreemptionDeniedError(OperationArbitrationError):
    """A caller other than an explicit user interrupt requested preemption."""


class OperationPreemptedError(OperationArbitrationError):
    """A running semantic operation yielded its session lease to an interrupt."""


@dataclass(slots=True)
class _Request:
    operation_id: str
    priority: IntentPriority
    sequence: int
    task: asyncio.Task[object]
    on_preempt: Callable[[str], Awaitable[None]] | None = None
    preempted_by: str | None = None


class SessionOperationArbiter:
    """Grant one exclusive, priority-ordered semantic operation lease.

    Priority orders queued work but never implicitly cancels a running goal.
    The sole preemption path is an explicit ``USER_INTERRUPT`` request.  It
    cancels the active operation task, waits for its ``finally`` cleanup to
    release the lease, and only then starts the interrupt body.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active: _Request | None = None
        self._pending: list[_Request] = []
        self._operation_ids: set[str] = set()
        self._next_sequence = 0

    async def run(
        self,
        operation_id: str,
        priority: IntentPriority,
        body: Callable[[], Awaitable[T]],
        *,
        preempt_active: bool = False,
        on_preempt: Callable[[str], Awaitable[None]] | None = None,
    ) -> T:
        """Run ``body`` while it exclusively owns the semantic session."""

        request = await self._acquire(
            operation_id,
            priority,
            preempt_active=preempt_active,
            on_preempt=on_preempt,
        )
        try:
            return await body()
        except asyncio.CancelledError:
            if request.preempted_by is not None:
                raise OperationPreemptedError(
                    f"operation {operation_id!r} was preempted by {request.preempted_by!r}"
                ) from None
            raise
        finally:
            await self._release(request)

    async def wait_until_pending(self, operation_ids: frozenset[str]) -> None:
        """Wait until the named operations are queued (diagnostics/tests)."""

        async with self._condition:
            await self._condition.wait_for(
                lambda: operation_ids.issubset(
                    request.operation_id for request in self._pending
                )
            )

    async def _acquire(
        self,
        operation_id: str,
        priority: IntentPriority,
        *,
        preempt_active: bool,
        on_preempt: Callable[[str], Awaitable[None]] | None,
    ) -> _Request:
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - asyncio always supplies one here
            raise RuntimeError("operation arbitration requires an asyncio task")
        async with self._condition:
            if operation_id in self._operation_ids:
                raise OperationArbitrationError(f"operation {operation_id!r} is already scheduled")
            if preempt_active and priority is not IntentPriority.USER_INTERRUPT:
                raise OperationPreemptionDeniedError(
                    "only an explicit USER_INTERRUPT operation may preempt the session"
                )
            active = self._active
            if preempt_active and active is not None and active.on_preempt is None:
                raise OperationPreemptionDeniedError(
                    f"active operation {active.operation_id!r} has no durable preemption hook"
                )
            if preempt_active and active is not None:
                await active.on_preempt(operation_id)

            request = _Request(
                operation_id,
                priority,
                self._next_sequence,
                task,
                on_preempt,
            )
            self._next_sequence += 1
            self._operation_ids.add(operation_id)
            self._pending.append(request)

            if preempt_active and active is not None:
                active.preempted_by = operation_id
                active.task.cancel(f"preempted by user interrupt {operation_id}")

            self._promote_if_idle()
            self._condition.notify_all()
            try:
                while self._active is not request:
                    await self._condition.wait()
                return request
            except BaseException:
                if request in self._pending:
                    self._pending.remove(request)
                elif self._active is request:
                    self._active = None
                self._operation_ids.discard(operation_id)
                self._promote_if_idle()
                self._condition.notify_all()
                raise

    async def _release(self, request: _Request) -> None:
        async with self._condition:
            if self._active is request:
                self._active = None
            elif request in self._pending:
                self._pending.remove(request)
            self._operation_ids.discard(request.operation_id)
            self._promote_if_idle()
            self._condition.notify_all()

    def _promote_if_idle(self) -> None:
        if self._active is not None or not self._pending:
            return
        request = min(self._pending, key=lambda item: (-int(item.priority), item.sequence))
        self._pending.remove(request)
        self._active = request


__all__ = [
    "OperationArbitrationError",
    "OperationPreemptedError",
    "OperationPreemptionDeniedError",
    "SessionOperationArbiter",
]
