"""Boundary wiring tests for Phase-2 instrumentation (#3 bus, #4 command dispatch).

Exercises two of the central instrumentation boundaries end-to-end against a real
``AdvancedLog`` writer and asserts the bulky rows land in their record families.
The writer is pinned via ``set_current_advanced_log`` and reset to the no-op
singleton in ``finally`` so it never leaks into other tests.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from pathlib import Path

from murder.app.service.command_dispatch import CommandDispatcher
from murder.bus import Bus, StateSnapshotEvent
from murder.bus.protocol import Entity
from murder.observability.advanced_log import (
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)
from murder.state.persistence.commands import enqueue_command
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import db_path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def test_bus_publish_and_command_dispatch_land_rows(tmp_path):
    repo = _repo(tmp_path)
    conn = get_db(db_path(repo))
    init_db(conn)
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-bnd", "2026-01-01T00:00:00", "{}"),
    )
    conn.commit()

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-bnd", "redacted")
        await log.start()
        set_current_advanced_log(log)
        try:
            # --- Boundary #3a: Bus.publish -> event_records ---
            bus = Bus("run-bnd")  # no db_conn: skip persist, exercise publish path
            await bus.publish(
                StateSnapshotEvent(run_id="run-bnd", agent_id="", entity=Entity.AGENT, key="*")
            )

            # --- Boundary #4: CommandDispatcher claim + complete -> command_records ---
            command_id = str(uuid.uuid4())
            enqueue_command(
                conn,
                command_id=command_id,
                run_id="run-bnd",
                agent_id="agent-1",
                role=None,
                ticket_id=None,
                target_worker="w-crow",
                kind="noop",
                payload={"hello": "world"},
                correlation_id="corr-1",
                idempotency_key="idem-1",
            )
            conn.commit()
            dispatcher = CommandDispatcher(conn=conn, repo_root=repo)
            claimed = dispatcher.claim_next(target_worker="w-crow", claimed_by="w-crow#0")
            assert claimed is not None and claimed.command_id == command_id
            dispatcher.complete(command_id, {"ok": True})
        finally:
            await log.stop()
            set_current_advanced_log(NullAdvancedLog())
        return log._db_path, command_id

    path, command_id = asyncio.run(_run())

    advconn = sqlite3.connect(str(path))
    advconn.row_factory = sqlite3.Row

    events = advconn.execute("SELECT * FROM event_records").fetchall()
    assert len(events) == 1, f"expected 1 event row, got {len(events)}"
    assert events[0]["event_id"] is not None  # log_context stamped the envelope
    assert '"state.snapshot"' in events[0]["payload"]

    commands = advconn.execute(
        "SELECT * FROM command_records ORDER BY id"
    ).fetchall()
    phases = [
        # payload is JSON; cheap substring assertion on the discriminator.
        "claim" if '"phase": "claim"' in c["payload"] else "complete"
        for c in commands
    ]
    assert "claim" in phases, f"missing claim row: {[c['payload'] for c in commands]}"
    assert "complete" in phases, f"missing complete row: {[c['payload'] for c in commands]}"
    # Bulky body captured on claim, result captured on complete.
    claim_row = next(c for c in commands if '"phase": "claim"' in c["payload"])
    assert '"hello": "world"' in claim_row["payload"]
    assert claim_row["command_id"] == command_id
    complete_row = next(c for c in commands if '"phase": "complete"' in c["payload"])
    assert '"ok": true' in complete_row["payload"]

    advconn.close()
