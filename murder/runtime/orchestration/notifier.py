"""Private in-process orchestration notifications.

This mechanism is deliberately non-authoritative: durable product state
belongs in repositories, facts, and projection inputs. It never crosses the
application transport.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from murder.observability.log_context import log_context
from murder.runtime.orchestration.events import OrchestrationEvent

log = logging.getLogger(__name__)


OrchestrationHandler = Callable[[OrchestrationEvent], Awaitable[None]]


class SubscriptionHandle:
    def __init__(self, notifier: InProcessOrchestrationEventSink, token: int) -> None:
        self._notifier = notifier
        self._token = token
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._notifier._subs.pop(self._token, None)


class InProcessOrchestrationEventSink:
    """Private, best-effort orchestration notification fan-out.

    It has no socket, replay, request/reply, or durable event-log semantics.
    """

    def __init__(self) -> None:
        self._subs: dict[int, OrchestrationHandler] = {}
        self._next_token = 0
        self._lock = asyncio.Lock()

    async def publish(self, event: OrchestrationEvent) -> None:
        # The flight recorder is NOT tapped here — it is a normal subscriber
        # (registered at Runtime.start), so the notifier stays unaware it exists. The
        # subscriber handler runs inside this ``log_context`` because ``_publish``
        # fans out via ``asyncio.gather``, which copies the active context into
        # each handler task — so the recorder still reads the right correlation
        # ids. See plan §2.5.A.
        event_id = getattr(event, "id", None)
        with log_context(event_id=str(event_id) if event_id is not None else None):
            await self._publish(event)

    async def _publish(self, event: OrchestrationEvent) -> None:
        async with self._lock:
            handlers = list(self._subs.values())

        # Fan out concurrently; each handler isolated.
        async def _dispatch(h: OrchestrationHandler) -> None:
            try:
                await h(event)
            except Exception:
                log.exception("orchestration notifier: handler raised on %s", event.type)

        await asyncio.gather(*(_dispatch(h) for h in handlers))

    def subscribe(self, handler: OrchestrationHandler) -> SubscriptionHandle:
        token = self._next_token
        self._next_token += 1
        self._subs[token] = handler
        return SubscriptionHandle(self, token)


__all__ = [
    "OrchestrationHandler",
    "InProcessOrchestrationEventSink",
    "SubscriptionHandle",
]
