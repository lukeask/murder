"""Structural guard + acceptance tests for the revised recorder (plan §2.5).

- ``record_family`` guard: every ``_BaseEvent`` subclass declares a family that
  is either a real table or ``None`` (explicit opt-out). Adding an event without
  a valid family fails HERE instead of silently never being captured — the
  analogue of the no-sqlite-in-TUI import guard.
- Bus routing is by family and captures EXACTLY ONCE (no parallel event_records
  dump), driven through a real ``Bus.publish`` so the correlation ids flow the
  production way (gather copies the publish context into the subscriber task).
- Backpressure is VISIBLE: a shed record bumps a per-family drop counter and a
  ``gap_marker`` row is emitted on the next record that fits.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from murder.bus import Bus
from murder.bus.protocol import SchedulerDecisionEvent, _BaseEvent
from murder.observability.advanced_log import (
    _FAMILY_EXTRA_COLUMNS,
    AdvancedLog,
    CommandRecord,
    open_advanced_log,
)
from murder.observability.log_context import set_run_id


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def _all_event_subclasses() -> set[type]:
    # __subclasses__() only sees classes whose defining module was imported. All
    # bus events live in murder.bus.protocol, which is imported above, so the
    # walk is complete today. If events ever move to another module, import it
    # here too — the floor assertion below fails loudly if the walk comes back
    # suspiciously empty.
    seen: set[type] = set()
    stack = list(_BaseEvent.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return seen


def test_every_bus_event_declares_a_valid_record_family() -> None:
    subclasses = _all_event_subclasses()
    # Sanity floor: catch a regression where the walk silently sees nothing
    # (e.g. an import reshuffle) and the guard below passes vacuously.
    assert len(subclasses) >= 10, (
        f"expected the full bus-event set; only saw {len(subclasses)} — "
        "is murder.bus.protocol imported?"
    )
    valid = set(_FAMILY_EXTRA_COLUMNS) | {None}
    offenders = {
        cls.__name__: getattr(cls, "record_family", "<<missing>>")
        for cls in subclasses
        if getattr(cls, "record_family", "<<missing>>") not in valid
    }
    assert not offenders, f"events with an unknown/missing record_family: {offenders}"


def test_decision_event_routes_to_its_family_exactly_once(tmp_path):
    repo = _repo(tmp_path)

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-dec", "redacted")
        await log.start()
        set_run_id("run-dec")
        bus = Bus("run-dec")  # no db: skip persist, exercise fan-out

        async def _recorder(event):
            log.record_bus_event(event)

        bus.subscribe(_recorder)
        await bus.publish(
            SchedulerDecisionEvent(
                run_id="run-dec",
                agent_id="scheduler",
                mode="crow_magic",
                harness="claude",
                window_key="w",
                decision=True,
                usage=0.5,
                t_until_reset=1.0,
                t_period=2.0,
                threshold=0.8,
                rationale="kick it",
                kicked_ticket_id="T-9",
            )
        )
        await log.stop()
        return log._db_path

    path = asyncio.run(_run())
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        decisions = conn.execute("SELECT * FROM decision_records").fetchall()
        events = conn.execute("SELECT * FROM event_records").fetchall()
        # Routed to its typed family, and NOT also dumped into event_records.
        assert len(decisions) == 1, decisions
        assert len(events) == 0, events
        row = decisions[0]
        assert row["run_id"] == "run-dec"  # ambient correlation id stamped
        assert row["event_id"] is not None  # publish stamped the envelope id
        assert "kick it" in row["payload"]
    finally:
        conn.close()


def test_backpressure_drop_is_visible_via_gap_marker(tmp_path):
    repo = _repo(tmp_path)

    async def _run() -> Path:
        log = AdvancedLog(
            repo.joinpath(".murder/advlogs/adv.db"), mode="redacted", run_id="r"
        )
        # Tiny bounded queue, drain NOT started: enqueues fill it deterministically.
        log._queue = asyncio.Queue(maxsize=4)
        for i in range(4):
            log.record_command(CommandRecord(phase="p", command_id=str(i)))
        # Queue is full; this one is shed and counted, not written.
        log.record_command(CommandRecord(phase="p", command_id="dropped"))
        assert log._drops["command_records"] == 1
        assert log.dropped == 1
        # Free two slots, then a record that fits also flushes the gap_marker.
        log._queue.get_nowait()
        log._queue.get_nowait()
        log.record_command(CommandRecord(phase="p", command_id="after"))
        assert log._drops["command_records"] == 0  # marker cleared the debt
        # Flush the queued rows straight to disk (deterministic, no drain loop /
        # sentinel race — the queue is full, which would shed the stop sentinel).
        log._flush_remaining()
        log._conn.close()
        return log._db_path

    path = asyncio.run(_run())
    conn = sqlite3.connect(str(path))
    try:
        payloads = [
            json.loads(p) for (p,) in conn.execute("SELECT payload FROM command_records")
        ]
    finally:
        conn.close()
    markers = [p for p in payloads if p.get("__gap_marker__")]
    assert len(markers) == 1, payloads
    assert markers[0]["dropped_since_last"] == 1
    assert markers[0]["family"] == "command_records"
