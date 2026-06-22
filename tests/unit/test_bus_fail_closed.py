"""Bus persistence is fail-closed (code-review item 4a).

The ``events`` table is the authoritative transport: socket subscribers are
DB-poll only, so an event that fails to persist would reach in-process handlers
but never replay to socket subscribers — a silent split-brain no caller can
detect. ``Bus._publish`` must therefore NOT fan out when persistence fails; it
raises so the failure is visible and uniform across all consumers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from murder.bus import Bus
from murder.bus.protocol import NoteEvent


class _BrokenConn:
    """Stand-in DB connection whose every write raises."""

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("disk full")


def test_publish_fails_closed_and_does_not_fan_out() -> None:
    async def scenario() -> None:
        bus = Bus(run_id="run", db_conn=_BrokenConn())
        delivered: list[Any] = []

        async def handler(event: Any) -> None:
            delivered.append(event)

        bus.subscribe(handler)

        raised = False
        try:
            await bus.publish(NoteEvent(run_id="run", note="hi"))
        except RuntimeError:
            raised = True

        # Persistence failure propagates...
        assert raised, "publish must raise when persistence fails"
        # ...and NO in-process handler saw the unpersisted event.
        assert delivered == [], "fail-closed: no fan-out on persistence failure"

    asyncio.run(scenario())
