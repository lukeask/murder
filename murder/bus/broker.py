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
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from murder.bus.protocol import BUS_EVENT_ADAPTER, BusEvent, EventFilter
from murder.facts.contracts import FactEnvelope, ProjectionInputRecord
from murder.facts.log import replay_facts, replay_projection_inputs

# Stable namespace for deriving a deterministic CommandEvent id from an
# events-table row when the originating commands row is gone (see
# ``_lookup_command_id``). Fixed so replay is reproducible across processes.
_COMMAND_ID_NAMESPACE = UUID("6f1d2c3a-0000-4000-8000-636d646e7400")
_DEFAULT_RETENTION_MIN_EVENTS = 20_000
_DEFAULT_RETENTION_MAX_AGE_DAYS = 7
_DEFAULT_RETENTION_CHECK_INTERVAL = 512

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
    """DB-backed compatibility broker with replay/tail and local RPC routing.

    ``events`` is deliberately not the retained fact log: it still contains
    transitional commands, notifications, decisions, and invalidations. Public
    fact subscriptions use the feature-owned methods below, which read only
    ``retained_facts``.
    """

    def __init__(
        self,
        bus: CallbackBus,
        db_conn: sqlite3.Connection,
        *,
        poll_interval_s: float = 0.05,
        retention_min_events: int = _DEFAULT_RETENTION_MIN_EVENTS,
        retention_max_age_days: int = _DEFAULT_RETENTION_MAX_AGE_DAYS,
        retention_check_interval: int = _DEFAULT_RETENTION_CHECK_INTERVAL,
    ) -> None:
        self._bus = bus
        self._db = db_conn
        self._poll_interval_s = poll_interval_s
        self._rpc_handlers: dict[str, Any] = {}
        self._retention_min_events = retention_min_events
        self._retention_max_age = timedelta(days=retention_max_age_days)
        self._retention_check_interval = max(1, retention_check_interval)
        self._publishes_since_retention = 0
        self._ensure_storage()

    async def publish(self, event: BusEvent) -> None:
        await self._bus.publish(event)
        self._publishes_since_retention += 1
        if self._publishes_since_retention >= self._retention_check_interval:
            self._publishes_since_retention = 0
            self.prune_retained_events()
            self.prune_projection_inputs()
            self.prune_retained_facts()

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
        return result

    def register_rpc_handler(self, target: str, handler: Any) -> None:
        self._rpc_handlers[target] = handler

    def unregister_rpc_handler(self, target: str) -> None:
        self._rpc_handlers.pop(target, None)

    def watermark(self) -> int:
        row = self._db.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
        return int(row["max_id"] if row is not None else 0)

    def current_max_id(self) -> int:
        """Return the current durable log position."""
        return self.watermark()

    def fact_watermark(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM retained_facts"
        ).fetchone()
        return int(row["max_sequence"] if row is not None else 0)

    def oldest_fact_sequence(self) -> int | None:
        row = self._db.execute(
            "SELECT MIN(sequence) AS min_sequence FROM retained_facts"
        ).fetchone()
        if row is None or row["min_sequence"] is None:
            return None
        return int(row["min_sequence"])

    def is_fact_cursor_retained(self, cursor: int) -> bool:
        if cursor < 0:
            return False
        watermark = self.fact_watermark()
        if cursor > watermark:
            return False
        oldest = self.oldest_fact_sequence()
        if oldest is None:
            return cursor == 0
        return cursor >= oldest - 1

    def replay_facts(
        self,
        *,
        since_sequence: int,
        kinds: frozenset[str] = frozenset(),
        until_sequence: int | None = None,
    ) -> tuple[FactEnvelope, ...]:
        records = replay_facts(
            self._db,
            after_sequence=since_sequence,
            kinds=kinds,
            until_sequence=until_sequence,
            limit=100_000,
        )
        return records

    async def tail_facts(
        self,
        *,
        since_sequence: int,
        kinds: frozenset[str] = frozenset(),
    ) -> AsyncIterator[FactEnvelope]:
        cursor = since_sequence
        while True:
            if not self.is_fact_cursor_retained(cursor):
                raise ReplayGapError(
                    f"fact cursor {cursor} is older than retained fact history"
                )
            records = self.replay_facts(
                since_sequence=cursor,
                kinds=kinds,
            )
            if records:
                for fact in records:
                    cursor = fact.sequence
                    yield fact
                continue
            await asyncio.sleep(self._poll_interval_s)

    def projection_watermark(self) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM projection_inputs"
        ).fetchone()
        return int(row["max_sequence"] if row is not None else 0)

    def oldest_projection_sequence(self) -> int | None:
        row = self._db.execute(
            "SELECT MIN(sequence) AS min_sequence FROM projection_inputs"
        ).fetchone()
        if row is None or row["min_sequence"] is None:
            return None
        return int(row["min_sequence"])

    def is_projection_cursor_retained(self, cursor: int) -> bool:
        if cursor < 0:
            return False
        watermark = self.projection_watermark()
        if cursor > watermark:
            return False
        oldest = self.oldest_projection_sequence()
        if oldest is None:
            return cursor == 0
        return cursor >= oldest - 1

    def replay_projection_inputs(
        self,
        *,
        since_sequence: int,
        projections: frozenset[str],
        until_sequence: int | None = None,
        limit: int = 1000,
    ) -> tuple[ProjectionInputRecord, ...]:
        return replay_projection_inputs(
            self._db,
            projections=projections,
            after_sequence=since_sequence,
            until_sequence=until_sequence,
            limit=limit,
        )

    async def tail_projection_inputs(
        self,
        *,
        since_sequence: int,
        projections: frozenset[str],
    ) -> AsyncIterator[ProjectionInputRecord]:
        cursor = since_sequence
        while True:
            if not self.is_projection_cursor_retained(cursor):
                raise ReplayGapError(
                    f"projection cursor {cursor} is older than retained projection history"
                )
            records = self.replay_projection_inputs(
                since_sequence=cursor,
                projections=projections,
            )
            if records:
                # Invalidations are key-only: within one poll batch only the
                # newest generation for a subject is useful. Socket writes are
                # awaited by the caller, providing bounded backpressure.
                cursor = records[-1].sequence
                latest = {
                    (record.projection, record.subject_key): record
                    for record in records
                }
                for record in sorted(latest.values(), key=lambda item: item.sequence):
                    yield record
                continue
            await asyncio.sleep(self._poll_interval_s)

    def projection_snapshot(self, projection: str) -> dict[str, object]:
        """Load authoritative current state for feature-owned projections."""

        if projection == "workflow_runs":
            from murder.state.persistence.workflow_runs import list_workflow_runs  # noqa: PLC0415

            return {
                "runs": [
                    run.model_dump(mode="json")
                    for run in list_workflow_runs(self._db)
                ]
            }
        if projection == "activities":
            from murder.state.persistence.activities import list_activities  # noqa: PLC0415

            return {
                "activities": [
                    activity.model_dump(mode="json")
                    for activity in list_activities(self._db)
                ]
            }
        if projection == "approvals":
            from murder.permissions.persistence import PermissionStore  # noqa: PLC0415

            return {
                "approvals": [
                    approval.model_dump(mode="json")
                    for approval in PermissionStore(self._db).list_approval_requests()
                ]
            }
        if projection == "permissions":
            from murder.permissions.persistence import PermissionStore  # noqa: PLC0415

            return {
                "permissions": [
                    grant.model_dump(mode="json")
                    for grant in PermissionStore(self._db).list_grants()
                ]
            }
        if projection == "sessions":
            from murder.runtime.sessions.persistence import SessionStore  # noqa: PLC0415

            store = SessionStore(self._db)
            sessions = store.list_sessions()
            return {
                "sessions": [
                    session.model_dump(mode="json")
                    for session in sessions
                ],
                "active_writer_leases": {
                    str(session.session_id): (
                        lease.model_dump(mode="json") if lease is not None else None
                    )
                    for session in sessions
                    for lease in (store.active_writer_lease(session.session_id),)
                },
                "writer_fences": {
                    str(session.session_id): store.writer_fence(session.session_id)
                    for session in sessions
                },
            }
        if projection == "schedule":
            from murder.app.service.client_api import dto_to_wire  # noqa: PLC0415
            from murder.app.service.schedule_snapshot import (  # noqa: PLC0415
                build_schedule_snapshot,
            )

            # No GenerationKeys in the broker path: derive a stable key from the
            # projection_inputs watermark (0 when the table is empty / unused).
            try:
                gen_row = self._db.execute(
                    """
                    SELECT COALESCE(MAX(generation), 0) AS gen
                      FROM projection_inputs
                     WHERE projection = 'schedule'
                    """
                ).fetchone()
                generation = int(gen_row["gen"]) if gen_row is not None else 0
            except sqlite3.OperationalError:
                generation = 0
            snapshot = build_schedule_snapshot(
                self._db,
                as_of=datetime.now(timezone.utc).replace(tzinfo=None),
                invalidation_key=f"schedule-{generation}",
            )
            return dto_to_wire(snapshot)
        raise ValueError(f"projection {projection!r} is not feature-owned")

    def oldest_event_id(self) -> int | None:
        """Return the oldest retained event id, or ``None`` when the log is empty."""
        row = self._db.execute("SELECT MIN(id) AS min_id FROM events").fetchone()
        if row is None or row["min_id"] is None:
            return None
        return int(row["min_id"])

    def is_cursor_retained(self, cursor: int) -> bool:
        """Return whether replaying from ``cursor`` can be satisfied from retention.

        A cursor points at the last event the client has observed. If the log's
        oldest retained row is ``N``, cursor ``N - 1`` is still valid because
        replay starts at ``id > cursor``.
        """
        if cursor < 0:
            return False
        watermark = self.watermark()
        if cursor > watermark:
            return False
        oldest_id = self.oldest_event_id()
        if oldest_id is None:
            return cursor == 0
        return cursor >= oldest_id - 1

    def prune_retained_events(self, *, now: datetime | None = None) -> int:
        """Apply event-log retention once and return the number of deleted rows.

        This produces the same retained set as repeatedly deleting the oldest
        event while the oldest event is older than the configured age and the
        log has more than the configured minimum count. The delete is batched
        into one statement so publish-triggered maintenance stays cheap.
        """
        count_row = self._db.execute("SELECT COUNT(*) AS n_events FROM events").fetchone()
        n_events = int(count_row["n_events"] if count_row is not None else 0)
        overage = n_events - self._retention_min_events
        if overage <= 0:
            return 0

        reference = now or datetime.now(timezone.utc)
        cutoff = reference - self._retention_max_age
        cutoff_text = cutoff.isoformat(timespec="seconds")
        cur = self._db.execute(
            """
            DELETE FROM events
             WHERE id IN (
                SELECT id
                  FROM events
                 WHERE ts < ?
                 ORDER BY id ASC
                 LIMIT ?
             )
            """,
            (cutoff_text, overage),
        )
        return int(cur.rowcount if cur.rowcount is not None else 0)

    def prune_retained_facts(self, *, now: datetime | None = None) -> int:
        """Delete old retained facts that no projection input still references."""

        count_row = self._db.execute(
            "SELECT COUNT(*) AS n_facts FROM retained_facts"
        ).fetchone()
        n_facts = int(count_row["n_facts"] if count_row is not None else 0)
        overage = n_facts - self._retention_min_events
        if overage <= 0:
            return 0

        reference = now or datetime.now(timezone.utc)
        cutoff = (reference - self._retention_max_age).isoformat(timespec="seconds")
        rows = self._db.execute(
            """
            SELECT f.fact_id
              FROM retained_facts AS f
             WHERE f.recorded_at < ?
               AND NOT EXISTS (
                    SELECT 1 FROM projection_inputs AS p
                     WHERE p.source_fact_id = f.fact_id
               )
             ORDER BY f.sequence ASC
             LIMIT ?
            """,
            (cutoff, overage),
        ).fetchall()
        fact_ids = [str(row["fact_id"]) for row in rows]
        if not fact_ids:
            return 0
        placeholders = ",".join("?" for _ in fact_ids)
        savepoint = f"fact_prune_{uuid4().hex}"
        self._db.execute(f"SAVEPOINT {savepoint}")
        try:
            cur = self._db.execute(
                f"DELETE FROM retained_facts WHERE fact_id IN ({placeholders})",
                tuple(fact_ids),
            )
        except BaseException:
            self._db.execute(f"ROLLBACK TO {savepoint}")
            self._db.execute(f"RELEASE {savepoint}")
            raise
        else:
            self._db.execute(f"RELEASE {savepoint}")
        return int(cur.rowcount if cur.rowcount is not None else 0)

    def prune_projection_inputs(self, *, now: datetime | None = None) -> int:
        """Prune the projection cursor log independently of public facts."""

        row = self._db.execute(
            "SELECT COUNT(*) AS n_inputs FROM projection_inputs"
        ).fetchone()
        count = int(row["n_inputs"] if row is not None else 0)
        overage = count - self._retention_min_events
        if overage <= 0:
            return 0
        reference = now or datetime.now(timezone.utc)
        cutoff = (reference - self._retention_max_age).isoformat(timespec="seconds")
        cur = self._db.execute(
            """
            DELETE FROM projection_inputs
             WHERE sequence IN (
                SELECT sequence
                  FROM projection_inputs
                 WHERE created_at < ?
                 ORDER BY sequence
                 LIMIT ?
             )
            """,
            (cutoff, overage),
        )
        return int(cur.rowcount if cur.rowcount is not None else 0)

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
        if filter is not None and filter.type is not None:
            sql += " AND type = ?"
            params.append(filter.type)
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

    def _ensure_storage(self) -> None:
        row = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).fetchone()
        if row is None:
            return
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_type_id ON events(type, id)"
        )

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
