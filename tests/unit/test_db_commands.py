from __future__ import annotations

import sqlite3

from murder import db as dbmod


def _seed_run(conn: sqlite3.Connection) -> None:
    dbmod.insert_run(conn, "r1", "{}")


def test_command_lifecycle_helpers(memdb: sqlite3.Connection) -> None:
    _seed_run(memdb)
    dbmod.enqueue_command(
        memdb,
        command_id="cmd-1",
        run_id="r1",
        agent_id="agent-1",
        role="crow",
        ticket_id="T-1",
        target_worker="collaborator",
        kind="collaborator.chat_send",
        payload={"text": "hi"},
        correlation_id="corr-1",
        idempotency_key="idem-1",
    )

    claimed = dbmod.claim_next_command(
        memdb, target_worker="collaborator", claimed_by="worker-1", lease_expires_at=50
    )
    assert claimed is not None
    assert claimed["id"] == "cmd-1"
    assert claimed["status"] == "in_flight"
    assert claimed["attempt_count"] == 1

    dbmod.complete_command(memdb, command_id="cmd-1", result={"ok": True})
    row = memdb.execute("SELECT status, result_json FROM commands WHERE id = 'cmd-1'").fetchone()
    assert row["status"] == "done"
    assert '"ok": true' in row["result_json"]


def test_fail_and_reap_helpers(memdb: sqlite3.Connection) -> None:
    _seed_run(memdb)
    dbmod.enqueue_command(
        memdb,
        command_id="cmd-2",
        run_id="r1",
        agent_id="agent-1",
        role="crow",
        ticket_id=None,
        target_worker="collaborator",
        kind="collaborator.chat_send",
        payload={},
        correlation_id="corr-2",
        idempotency_key="idem-2",
        status="in_flight",
        claimed_by="worker-1",
        lease_expires_at=10,
    )
    reaped = dbmod.reap_stale_commands(memdb, now_epoch=10)
    assert reaped == {"retried": ["cmd-2"], "failed": []}
    row = memdb.execute(
        "SELECT status, claimed_by, lease_expires_at, attempt_count "
        "FROM commands WHERE id = 'cmd-2'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None
    assert row["lease_expires_at"] is None
    assert row["attempt_count"] == 1

    dbmod.fail_command(memdb, command_id="cmd-2", last_error="boom", retryable=False)
    failed = memdb.execute(
        "SELECT status, retryable, last_error FROM commands WHERE id = 'cmd-2'"
    ).fetchone()
    assert failed["status"] == "failed"
    assert failed["retryable"] == 0
    assert failed["last_error"] == "boom"


def test_reap_stale_commands_fails_exhausted_or_non_retryable(
    memdb: sqlite3.Connection,
) -> None:
    _seed_run(memdb)
    for command_id, retryable, attempt_count in [
        ("cmd-retry-exhausted", True, 2),
        ("cmd-non-retryable", False, 0),
    ]:
        dbmod.enqueue_command(
            memdb,
            command_id=command_id,
            run_id="r1",
            agent_id="agent-1",
            role="crow",
            ticket_id=None,
            target_worker="collaborator",
            kind="collaborator.chat_send",
            payload={},
            correlation_id=f"corr-{command_id}",
            idempotency_key=f"idem-{command_id}",
            status="in_flight",
            claimed_by="worker-1",
            lease_expires_at=10,
            retryable=retryable,
            attempt_count=attempt_count,
        )

    reaped = dbmod.reap_stale_commands(memdb, now_epoch=10)

    assert reaped == {
        "retried": [],
        "failed": ["cmd-non-retryable", "cmd-retry-exhausted"],
    }


def test_worker_heartbeat_and_sentinel_state_helpers(memdb: sqlite3.Connection) -> None:
    _seed_run(memdb)
    dbmod.upsert_worker_heartbeat(
        memdb,
        worker_id="worker-1",
        run_id="r1",
        role="collaborator",
        payload={"ok": True},
    )
    row = dbmod.get_worker_heartbeat(memdb, "worker-1")
    assert row is not None
    assert row["run_id"] == "r1"
    assert row["role"] == "collaborator"
    assert '"ok": true' in row["payload_json"]

    dbmod.upsert_sentinel_state(
        memdb,
        key="global",
        run_id="r1",
        state={"active": 2},
    )
    state = dbmod.get_sentinel_state(memdb, "global")
    assert state == {"active": 2}
