"""Tests for the history view: read-model derivation + the dismiss op.

The history feed is a read model over the durable ``conversation_blocks
kind='user'`` spine (no new write at the send boundary). These tests cover the
zero-LLM status derivation (open/stale/dismissed), the server-side noise filter,
loose-thread ordering, the resumable flag, and the dismiss op round-trip.

Convention: ``asyncio.run()`` for async (no ``@pytest.mark.asyncio``).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from murder.app.service.read_model import STALE_AFTER_HOURS, ServiceReadModel
from murder.bus import Entity
from murder.runtime.orchestration.history_ops import HistoryOps
from murder.state.persistence import conversation, history
from murder.state.persistence.schema import get_db, init_db


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    conn = get_db(p)
    init_db(conn)
    conn.close()
    return p


def _conn(db_path: Path) -> sqlite3.Connection:
    return get_db(db_path)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _add_user(
    conn: sqlite3.Connection,
    agent_id: str,
    text: str,
    *,
    received_at: str | None = None,
) -> None:
    conversation.append_user_message(conn, agent_id, text, received_at=received_at)


def test_open_status_for_recent_message(db_path: Path) -> None:
    conn = _conn(db_path)
    _add_user(conn, "collaborator", "fix the empty pane case")
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    assert len(snap.items) == 1
    assert snap.items[0].status == "open"
    assert snap.items[0].text == "fix the empty pane case"
    assert snap.items[0].target == "collaborator"
    assert snap.items[0].conversation_id == "collaborator"


def test_history_item_carries_conversation_id_separate_from_target(db_path: Path) -> None:
    conn = _conn(db_path)
    conversation.append_user_message(
        conn,
        "crow-t1",
        "resume the prior session",
        conversation_id="conv-uuid-1",
    )
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    assert len(snap.items) == 1
    assert snap.items[0].target == "crow-t1"
    assert snap.items[0].conversation_id == "conv-uuid-1"
    assert snap.items[0].item_id == "conv-uuid-1:0"


def test_stale_status_for_old_message(db_path: Path) -> None:
    conn = _conn(db_path)
    old = _iso(datetime.utcnow() - timedelta(hours=STALE_AFTER_HOURS + 1))
    _add_user(conn, "collaborator", "remember to prune worktrees", received_at=old)
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    assert len(snap.items) == 1
    assert snap.items[0].status == "stale"


def test_dismissed_status_overrides_age(db_path: Path) -> None:
    conn = _conn(db_path)
    old = _iso(datetime.utcnow() - timedelta(hours=STALE_AFTER_HOURS + 1))
    _add_user(conn, "collaborator", "an old but dismissed thread", received_at=old)
    conn.commit()
    # item_id is "<conversation_id>:<ordinal>"; first user block is ordinal 0.
    history.set_history_status(conn, "collaborator:0", "dismissed")
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    assert len(snap.items) == 1
    assert snap.items[0].status == "dismissed"


def test_noise_filter_drops_commands_keeps_at(db_path: Path) -> None:
    conn = _conn(db_path)
    _add_user(conn, "collaborator", "@focus look at this")  # kept
    _add_user(conn, "collaborator", "!ls -la")  # dropped (leading !)
    _add_user(conn, "collaborator", ":wq")  # dropped (leading :)
    _add_user(conn, "collaborator", "   ")  # dropped (whitespace -> no block)
    _add_user(conn, "collaborator", "a real intention")  # kept
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    texts = {i.text for i in snap.items}
    assert texts == {"@focus look at this", "a real intention"}


def test_loose_thread_ordering_is_newest_first(db_path: Path) -> None:
    conn = _conn(db_path)
    t0 = datetime.utcnow() - timedelta(hours=10)
    _add_user(conn, "collaborator", "first", received_at=_iso(t0))
    _add_user(conn, "collaborator", "second", received_at=_iso(t0 + timedelta(hours=1)))
    _add_user(conn, "collaborator", "third", received_at=_iso(t0 + timedelta(hours=2)))
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    # The read model returns newest-first; assert the snapshot's wire order here.
    assert [i.text for i in snap.items] == ["third", "second", "first"]


def test_resumable_flag(db_path: Path) -> None:
    conn = _conn(db_path)
    # Append the user turn FIRST, then stamp harness/session/status — append_user_message
    # upserts the conversation row with the default 'in_progress' status, so the
    # terminal-state writes must come after it (mirrors the real graceful-exit order).
    # Resumable: claude_code + status complete + harness_session_id present.
    _add_user(conn, "crow-t1", "do the thing")
    conversation.upsert_conversation(
        conn,
        conversation_id="crow-t1",
        agent_id="crow-t1",
        harness="claude_code",
        harness_session_id="sess-abc",
    )
    conversation.set_conversation_status(conn, "crow-t1", "complete")
    # Not resumable: still in_progress.
    _add_user(conn, "crow-t2", "another thing")
    conversation.upsert_conversation(
        conn,
        conversation_id="crow-t2",
        agent_id="crow-t2",
        harness="claude_code",
        harness_session_id="sess-def",
    )
    # Not resumable: wrong harness.
    _add_user(conn, "crow-t3", "third thing")
    conversation.upsert_conversation(
        conn,
        conversation_id="crow-t3",
        agent_id="crow-t3",
        harness="cursor",
        harness_session_id="sess-ghi",
    )
    conversation.set_conversation_status(conn, "crow-t3", "complete")
    conn.commit()
    snap = ServiceReadModel(db_path).get_history_snapshot()
    by_target = {i.target: i for i in snap.items}
    assert by_target["crow-t1"].resumable is True
    assert by_target["crow-t1"].conversation_status == "complete"
    assert by_target["crow-t1"].harness == "claude_code"
    assert by_target["crow-t2"].resumable is False
    assert by_target["crow-t3"].resumable is False


class _FakeHost:
    """Minimal OrchestratorHost stand-in for the dismiss op."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.published: list[tuple[Entity, str]] = []

    async def publish_snapshot(self, entity: Entity, key: str) -> None:
        self.published.append((entity, key))


def test_dismiss_op_round_trip(db_path: Path) -> None:
    conn = _conn(db_path)
    _add_user(conn, "collaborator", "dismiss me")
    conn.commit()
    rm = ServiceReadModel(db_path)
    assert rm.get_history_snapshot().items[0].status == "open"

    host = _FakeHost(conn)
    ops = HistoryOps(host)  # type: ignore[arg-type]
    result: dict[str, Any] = asyncio.run(ops.dismiss("collaborator:0"))
    conn.commit()

    assert result == {"item_id": "collaborator:0", "status": "dismissed"}
    assert host.published == [(Entity.HISTORY, "collaborator:0")]
    assert rm.get_history_snapshot().items[0].status == "dismissed"
