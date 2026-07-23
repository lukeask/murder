"""Private in-process orchestration notifications.

This mechanism is deliberately non-authoritative: durable product state
belongs in repositories, facts, and projection inputs. It never crosses the
application transport.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from murder.bus.protocol import (
    COMMAND_REAPER_INTERVAL_S,
    DEFAULT_LEASE_TTL_S,
    DEFAULT_MAX_COMMAND_ATTEMPTS,
    AgentLifecycleEvent,
    AgentStatus,
    CommandEvent,
    CommandStatus,
    CompletionVerdictEvent,
    ConversationBlockEvent,
    ConversationStateEvent,
    ErrorEvent,
    EscalationEvent,
    HeartbeatEvent,
    OrchestrationEvent,
    Role,
    StatusChangeEvent,
    SummaryEvent,
)
from murder.observability.log_context import log_context
from murder.work.tickets.status import TicketStatus

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger("murder.bus")


OrchestrationHandler = Callable[[OrchestrationEvent], Awaitable[None]]


class SubscriptionHandle:
    def __init__(self, notifier: OrchestrationNotifier, token: int) -> None:
        self._notifier = notifier
        self._token = token
        self._cancelled = False

    def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        self._notifier._subs.pop(self._token, None)


class OrchestrationNotifier:
    """Private, best-effort orchestration notification fan-out.

    It has no socket, replay, request/reply, or durable event-log semantics.
    """

    def __init__(self, run_id: str, db_conn: sqlite3.Connection | None = None) -> None:
        self._run_id = run_id
        # Only the command-work repository is retained. General notifications
        # are never appended to the old events table.
        self._command_db = db_conn
        self._subs: dict[int, OrchestrationHandler] = {}
        self._next_token = 0
        self._lock = asyncio.Lock()

    async def publish(self, event: OrchestrationEvent) -> None:
        # The flight recorder is NOT tapped here — it is a normal subscriber
        # (registered at Runtime.start), so the bus stays unaware it exists. The
        # subscriber handler runs inside this ``log_context`` because ``_publish``
        # fans out via ``asyncio.gather``, which copies the active context into
        # each handler task — so the recorder still reads the right correlation
        # ids. See plan §2.5.A.
        event_id = getattr(event, "id", None)
        with log_context(event_id=str(event_id) if event_id is not None else None):
            await self._publish(event)

    async def _publish(self, event: OrchestrationEvent) -> None:
        # Commands are durable workflow work items, not a replayable event
        # stream. Their repository is the commands table; all other transient
        # orchestration notifications remain memory-only.
        if isinstance(event, CommandEvent) and self._command_db is not None:
            from murder.state.persistence.commands import insert_command_event

            insert_command_event(
                self._command_db,
                command_id=str(event.id),
                run_id=event.run_id,
                agent_id=event.agent_id,
                role=None,
                ticket_id=getattr(event, "ticket_id", None),
                target_worker=event.target_worker,
                kind=event.kind,
                payload=event.payload,
                correlation_id=event.correlation_id,
                idempotency_key=event.idempotency_key,
                status=event.status.value,
                claimed_by=event.claimed_by,
                lease_expires_at=event.lease_expires_at,
                attempt_count=event.attempt_count,
                retryable=event.retryable,
                result=event.result,
                event_type=event.type,
                event_payload={},
                ts=event.ts.isoformat(timespec="seconds"),
            )
        async with self._lock:
            handlers = list(self._subs.values())

        # Fan out concurrently; each handler isolated.
        async def _dispatch(h: OrchestrationHandler) -> None:
            try:
                await h(event)
            except Exception:
                log.exception("bus: handler raised on %s", event.type)

        await asyncio.gather(*(_dispatch(h) for h in handlers))

    def subscribe(self, handler: OrchestrationHandler) -> SubscriptionHandle:
        token = self._next_token
        self._next_token += 1
        self._subs[token] = handler
        return SubscriptionHandle(self, token)


__all__ = [
    # In-process notification fan-out
    "OrchestrationNotifier",
    "SubscriptionHandle",
    "OrchestrationHandler",
    # Re-exports from protocol
    "Role",
    "TicketStatus",
    "AgentStatus",
    "CommandStatus",
    "HeartbeatEvent",
    "SummaryEvent",
    "EscalationEvent",
    "StatusChangeEvent",
    "ErrorEvent",
    "CommandEvent",
    "CompletionVerdictEvent",
    "AgentLifecycleEvent",
    "ConversationBlockEvent",
    "ConversationStateEvent",
    "OrchestrationEvent",
    "DEFAULT_LEASE_TTL_S",
    "DEFAULT_MAX_COMMAND_ATTEMPTS",
    "COMMAND_REAPER_INTERVAL_S",
]
