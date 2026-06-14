"""Transport-neutral bus broker interfaces.

This module is intentionally small: protocol types live in
``murder.bus.protocol`` and the legacy in-process callback broker still lives
in ``murder.bus``.  New workers and clients should depend on this interface so
they are not coupled to the current single-process runtime shape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid5

from murder.bus.protocol import BUS_EVENT_ADAPTER, BusEvent, EventFilter
from murder.observability.advanced_log import current_advanced_log

# Stable namespace for deriving a deterministic CommandEvent id from an
# events-table row when the originating commands row is gone (see
# ``_lookup_command_id``). Fixed so replay is reproducible across processes.
_COMMAND_ID_NAMESPACE = UUID("6f1d2c3a-0000-4000-8000-636d646e7400")

LOGGER = logging.getLogger(__name__)


class BusBroker(Protocol):
    """Common surface for in-process and socket-backed brokers."""

    async def publish(self, event: BusEvent) -> None:
        """Persist and fan out ``event``."""

    def subscribe(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int | None = None,
    ) -> AsyncIterator[BusEvent]:
        """Yield replayed then live events matching ``filter``."""

    async def request(
        self,
        target: str,
        body: dict,
        *,
        timeout_s: float,
    ) -> dict:
        """Send an RPC-style request and return its reply."""


class CallbackSubscription(Protocol):
    def cancel(self) -> None:
        """Cancel the callback subscription."""


class CallbackBus(Protocol):
    async def publish(self, event: Any) -> None:
        """Publish one event."""

    def subscribe(
        self,
        handler: Any,
        filter: EventFilter | None = None,
    ) -> CallbackSubscription:
        """Subscribe a callback handler."""


class UnsupportedReplayError(RuntimeError):
    """Raised when a broker cannot satisfy a replaying subscription yet."""


class UnsupportedRpcError(RuntimeError):
    """Raised when a broker does not implement request/reply yet."""


class ReplayGapError(RuntimeError):
    """Raised when replay persistence is unavailable."""


class InProcessBroker:
    """Async-iterator adapter around the current callback-style ``Bus``.

    The adapter applies filters in the underlying ``Bus`` subscription, so
    subscribers are not handed irrelevant events. Historical replay is left to
    the durable broker implementation that can read the ``events`` table.
    """

    def __init__(self, bus: CallbackBus, *, queue_size: int = 1024) -> None:
        self._bus = bus
        self._queue_size = queue_size
        self._dropped = 0

    async def publish(self, event: BusEvent) -> None:
        await self._bus.publish(event)

    @property
    def dropped_count(self) -> int:
        """Number of events dropped from full subscriber queues so far."""
        return self._dropped

    @asynccontextmanager
    async def _subscription(
        self,
        filter: EventFilter | None,
    ) -> AsyncIterator[asyncio.Queue[BusEvent]]:
        queue: asyncio.Queue[BusEvent] = asyncio.Queue(maxsize=self._queue_size)

        async def _handler(event: BusEvent) -> None:
            if queue.full():
                # Drop-oldest on a slow consumer. The consumer has no gap
                # signal, so at least log + expose a counter so the loss is
                # observable rather than silent.
                await queue.get()
                self._dropped += 1
                LOGGER.warning(
                    "InProcessBroker subscriber queue full; dropped oldest "
                    "event (total dropped=%d)",
                    self._dropped,
                )
            await queue.put(event)

        handle = self._bus.subscribe(_handler, filter)
        try:
            yield queue
        finally:
            handle.cancel()

    async def subscribe(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int | None = None,
    ) -> AsyncIterator[BusEvent]:
        if since_id is not None:
            raise UnsupportedReplayError(
                "InProcessBroker cannot replay persisted events; use the durable broker"
            )
        async with self._subscription(filter) as queue:
            while True:
                yield await queue.get()

    async def request(
        self,
        target: str,
        body: dict,
        *,
        timeout_s: float,
    ) -> dict:
        raise UnsupportedRpcError(f"InProcessBroker has no RPC router for target {target!r} yet")


class DurableBroker:
    """DB-backed broker with replay/tail and in-process RPC routing."""

    def __init__(
        self,
        bus: CallbackBus,
        db_conn: sqlite3.Connection,
        *,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._bus = bus
        self._db = db_conn
        self._poll_interval_s = poll_interval_s
        self._rpc_handlers: dict[str, Any] = {}

    async def publish(self, event: BusEvent) -> None:
        await self._bus.publish(event)

    async def subscribe(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int | None = None,
    ) -> AsyncIterator[BusEvent]:
        cursor = since_id or 0
        for row_id, event in self.replay(
            filter,
            since_id=cursor,
            until_id=self.watermark(),
        ):
            cursor = row_id
            yield event
        async for row_id, event in self.tail(filter, since_id=cursor):
            cursor = row_id
            yield event

    async def request(
        self,
        target: str,
        body: dict,
        *,
        timeout_s: float,
    ) -> dict:
        # v0 limitation: RPC is server-process-local only. Handlers live in
        # this process's ``_rpc_handlers`` dict; there is no routing to a
        # handler hosted in a *different* worker process. A SocketBusClient in
        # another process can reach handlers registered server-side, but
        # worker-to-worker / client-hosted RPC is not yet implemented and
        # surfaces here as UnsupportedRpcError.
        handler = self._rpc_handlers.get(target)
        if handler is None:
            raise UnsupportedRpcError(f"No RPC handler registered for target {target!r}")

        async def _invoke() -> dict:
            result = handler(body)
            if asyncio.iscoroutine(result):
                result = await result
            if not isinstance(result, dict):
                raise TypeError(f"RPC handler {target!r} returned non-dict result")
            return result

        result = await asyncio.wait_for(_invoke(), timeout=timeout_s)
        current_advanced_log().record_event(
            payload={
                "kind": "broker.request",
                "target": target,
                "body": body,
                "result": result,
            }
        )
        return result

    def register_rpc_handler(self, target: str, handler: Any) -> None:
        self._rpc_handlers[target] = handler

    def unregister_rpc_handler(self, target: str) -> None:
        self._rpc_handlers.pop(target, None)

    def watermark(self) -> int:
        row = self._db.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
        return int(row["max_id"] if row is not None else 0)

    def replay(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int,
        until_id: int | None = None,
    ) -> list[tuple[int, BusEvent]]:
        if self._db is None:
            raise ReplayGapError("Durable replay requires a DB connection")
        sql = (
            "SELECT id, ts, run_id, agent_id, role, ticket_id, type, payload_json "
            "FROM events WHERE id > ?"
        )
        params: list[Any] = [since_id]
        if until_id is not None:
            sql += " AND id <= ?"
            params.append(until_id)
        sql += " ORDER BY id ASC"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        out: list[tuple[int, BusEvent]] = []
        for row in rows:
            event = self._event_from_row(row)
            if filter is not None and not filter.matches(event):
                continue
            out.append((int(row["id"]), event))
        return out

    async def tail(
        self,
        filter: EventFilter | None = None,
        *,
        since_id: int,
    ) -> AsyncIterator[tuple[int, BusEvent]]:
        # v0 scaling limitation: live fan-out to socket subscribers is driven
        # by polling the events table every ``poll_interval_s`` (default 50ms),
        # NOT by the in-process Bus.publish callback fan-out. This costs every
        # subscriber a 0–poll_interval latency tax and an O(clients × poll-rate)
        # query load against the shared SQLite handle; an event that is
        # published but never persisted is invisible here forever. A
        # notify/condition-variable wakeup is the right fix before tier-3 scale.
        cursor = since_id
        while True:
            rows = self.replay(filter, since_id=cursor)
            if rows:
                for row_id, event in rows:
                    cursor = row_id
                    yield row_id, event
                continue
            await asyncio.sleep(self._poll_interval_s)

    def _event_from_row(self, row: sqlite3.Row) -> BusEvent:
        payload_raw = row["payload_json"]
        payload = json.loads(payload_raw) if payload_raw else {}
        if not isinstance(payload, dict):
            payload = {}
        data: dict[str, Any] = dict(payload)
        data["type"] = row["type"]
        data["run_id"] = row["run_id"]
        data["agent_id"] = row["agent_id"] or ""
        role = row["role"]
        if role:
            data["role"] = role
        ticket_id = row["ticket_id"]
        if ticket_id is not None:
            data["ticket_id"] = ticket_id
        ts = row["ts"]
        if isinstance(ts, str) and ts:
            try:
                datetime.fromisoformat(ts)
                data["ts"] = ts
            except ValueError:
                data["ts"] = datetime.now(timezone.utc).isoformat()
        if row["type"] == "command":
            data["id"] = self._lookup_command_id(data, row)
        return BUS_EVENT_ADAPTER.validate_python(data)

    def _lookup_command_id(self, payload: dict[str, Any], row: sqlite3.Row) -> UUID:
        # The CommandEvent UUID is excluded from payload_json on persist; it is
        # recoverable from the commands table, keyed by the (unique)
        # idempotency_key, where it is stored as the TEXT primary key.
        key = payload.get("idempotency_key")
        if isinstance(key, str):
            cmd = self._db.execute(
                "SELECT id FROM commands WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if cmd is not None:
                try:
                    return UUID(str(cmd["id"]))
                except ValueError:
                    pass
        # The commands row was reaped/deleted (or the key was missing). The
        # events.id is an integer autoincrement, not a UUID, so deriving one
        # deterministically (uuid5 over the row id) keeps replay reproducible —
        # a random uuid4() would invent a fresh id on every replay of the same
        # durable row.
        return uuid5(_COMMAND_ID_NAMESPACE, str(row["id"]))


__all__ = [
    "BusBroker",
    "InProcessBroker",
    "DurableBroker",
    "ReplayGapError",
    "UnsupportedReplayError",
    "UnsupportedRpcError",
]
