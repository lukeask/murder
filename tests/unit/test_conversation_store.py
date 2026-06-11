"""Tests for murder.state.persistence.conversation.

COOKBOOK = canonical persist→read flow: upsert a conversation, append blocks,
read them back.  Shows the intended API shape and is copyable as a starting
point for callers.

EDGE CASES = reconciliation and failure modes for the mature persistence
contract: merge_conversation_doc semantics (shorter-ignored /
equal-count-update / longer-replaces / live-block rule), status transitions,
init_db idempotency, CC fixture round-trip.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from murder.state.persistence.conversation import (
    BLOCK_KINDS,
    append_block,
    mark_stale_conversations,
    merge_conversation_doc,
    read_conversation_blocks,
    read_conversation_doc,
    segment_to_block_kind,
    set_conversation_status,
    set_harness_session_id,
    update_live_block,
    upsert_conversation,
)
from murder.state.persistence.schema import get_db, init_db

_CC_EXPECTED = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cc" / "expected.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    """In-memory DB initialised with the full murder schema."""
    db = get_db(tmp_path / "test.db")
    init_db(db)
    return db


def _make_conv(
    conn: sqlite3.Connection, conv_id: str = "conv-1", agent_id: str = "agent-1"
) -> None:
    upsert_conversation(
        conn,
        conversation_id=conv_id,
        agent_id=agent_id,
        harness="cc",
        model="opus",
        status="in_progress",
    )


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_persist_and_read_back_conversation(conn):
    """Canonical usage: upsert → append blocks → read_conversation_doc round-trip.

    Copyable as a starting point for any caller that writes and then reads
    conversation state.
    """
    # TODO: factory_segment in factories.py
    upsert_conversation(
        conn,
        conversation_id="conv-1",
        agent_id="agent-1",
        harness="cc",
        model="opus",
        status="in_progress",
    )

    segs = [
        {"type": "user", "text": "hi"},
        {"type": "assistant", "phase": "final", "text": "hello"},
    ]
    for seg in segs:
        append_block(conn, "conv-1", seg)

    doc = read_conversation_doc(conn, "conv-1")
    assert doc is not None
    assert doc["harness"] == "cc"
    assert len(doc["segments"]) == 2
    assert doc["segments"][0] == segs[0]
    assert doc["segments"][1] == segs[1]

    blocks = read_conversation_blocks(conn, "conv-1")
    assert len(blocks) == 2
    assert blocks[0].payload == segs[0]
    assert blocks[1].payload == segs[1]


def test_merge_conversation_doc_roundtrip_cc_fixture(conn):
    """Round-trip the full CC fixture through merge → read_conversation_doc."""
    original_doc = json.loads(_CC_EXPECTED.read_text(encoding="utf-8"))
    _make_conv(conn, conv_id="conv-cc")

    merge_conversation_doc(conn, "conv-cc", original_doc)

    recovered = read_conversation_doc(conn, "conv-cc")
    assert recovered is not None
    assert recovered["harness"] == original_doc["harness"]
    # All segments are stored (live trailing block is the last one).
    stored_segments = recovered["segments"]
    assert len(stored_segments) == len(original_doc["segments"])
    # Payloads must match exactly — lossless round-trip.
    for stored, orig in zip(stored_segments, original_doc["segments"]):
        assert stored == orig


# ============================================================
# === EDGE CASES =============================================
# ============================================================


# --- segment_to_block_kind ---


@pytest.mark.parametrize(
    "seg,expected_kind",
    [
        ({"type": "user", "text": "hi"}, "user"),
        ({"type": "tool_call", "name": "Bash"}, "tool_call"),
        (
            {"type": "assistant", "phase": "intermediate", "text": "thinking..."},
            "assistant_intermediate",
        ),
        ({"type": "assistant", "phase": "final", "text": "done"}, "assistant_final"),
    ],
)
def test_segment_to_block_kind_maps_segment_type_to_kind(seg, expected_kind):
    assert segment_to_block_kind(seg) == expected_kind


def test_segment_to_block_kind_all_block_kinds_valid():
    """Every BLOCK_KINDS entry must be a valid kind recognised by the schema."""
    valid = {
        "user",
        "assistant_intermediate",
        "assistant_final",
        "tool_call",
        "plan_update",
        "agent_event",
        "choice_prompt",
        "notice",
    }
    assert set(BLOCK_KINDS) == valid


# --- upsert_conversation ---


def test_upsert_creates_row(conn):
    _make_conv(conn)
    row = conn.execute("SELECT * FROM conversations WHERE conversation_id = 'conv-1'").fetchone()
    assert row is not None
    assert row["agent_id"] == "agent-1"
    assert row["harness"] == "cc"
    assert row["model"] == "opus"
    assert row["status"] == "in_progress"


def test_upsert_idempotent_does_not_duplicate(conn):
    _make_conv(conn)
    _make_conv(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE conversation_id = 'conv-1'"
    ).fetchone()[0]
    assert count == 1


def test_upsert_updates_harness_on_second_call(conn):
    _make_conv(conn)
    upsert_conversation(conn, conversation_id="conv-1", agent_id="agent-1", harness="codex")
    row = conn.execute(
        "SELECT harness FROM conversations WHERE conversation_id = 'conv-1'"
    ).fetchone()
    assert row["harness"] == "codex"


def test_upsert_stores_timestamps(conn):
    _make_conv(conn)
    row = conn.execute(
        "SELECT created_at, updated_at FROM conversations WHERE conversation_id = 'conv-1'"
    ).fetchone()
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


# --- set_conversation_status ---


@pytest.mark.parametrize(
    "transitions",
    [
        ["complete"],
        ["stale"],
        ["complete", "in_progress"],
    ],
)
def test_set_status_applies_transition(conn, transitions):
    """Status setter stores each value correctly across all legal transitions."""
    _make_conv(conn)
    for status in transitions:
        set_conversation_status(conn, "conv-1", status)
    row = conn.execute("SELECT status FROM conversations WHERE conversation_id='conv-1'").fetchone()
    assert row["status"] == transitions[-1]


# --- set_harness_session_id ---


def test_set_harness_session_id_stores_value(conn):
    _make_conv(conn)
    set_harness_session_id(conn, "conv-1", "sess-abc123")
    row = conn.execute(
        "SELECT harness_session_id FROM conversations WHERE conversation_id='conv-1'"
    ).fetchone()
    assert row["harness_session_id"] == "sess-abc123"


def test_set_harness_session_id_updates_updated_at(conn):
    _make_conv(conn)
    row_before = conn.execute(
        "SELECT updated_at FROM conversations WHERE conversation_id='conv-1'"
    ).fetchone()
    set_harness_session_id(conn, "conv-1", "sess-xyz")
    row_after = conn.execute(
        "SELECT updated_at FROM conversations WHERE conversation_id='conv-1'"
    ).fetchone()
    # updated_at must be at least as recent (seconds precision might be equal in fast tests)
    assert row_after["updated_at"] >= row_before["updated_at"]


# --- append_block ---


def test_append_block_creates_row(conn):
    _make_conv(conn)
    seg = {"type": "user", "text": "hello"}
    block = append_block(conn, "conv-1", seg)
    assert block.id is not None
    assert block.kind == "user"
    assert block.ordinal == 0
    assert block.sealed is True  # user blocks seal immediately
    assert block.payload == seg


def test_append_block_increments_ordinal(conn):
    _make_conv(conn)
    b0 = append_block(conn, "conv-1", {"type": "user", "text": "q"})
    b1 = append_block(conn, "conv-1", {"type": "assistant", "phase": "intermediate", "text": "a"})
    assert b0.ordinal == 0
    assert b1.ordinal == 1


def test_append_block_assistant_intermediate_not_sealed(conn):
    _make_conv(conn)
    seg = {"type": "assistant", "phase": "intermediate", "text": "..."}
    block = append_block(conn, "conv-1", seg)
    assert block.sealed is False


def test_append_block_assistant_final_sealed(conn):
    _make_conv(conn)
    seg = {"type": "assistant", "phase": "final", "text": "done"}
    block = append_block(conn, "conv-1", seg)
    assert block.sealed is True


def test_append_block_seals_previous_live_block(conn):
    """Appending a second segment seals the prior live block."""
    _make_conv(conn)
    b0 = append_block(conn, "conv-1", {"type": "assistant", "phase": "intermediate", "text": "..."})
    assert b0.sealed is False
    # append a new segment — should seal b0
    append_block(conn, "conv-1", {"type": "user", "text": "next"}, seal_previous=True)
    row = conn.execute("SELECT sealed FROM conversation_blocks WHERE id = ?", (b0.id,)).fetchone()
    assert row["sealed"] == 1


def test_append_block_stores_timestamp(conn):
    _make_conv(conn)
    block = append_block(
        conn, "conv-1", {"type": "user", "text": "t"}, received_at="2026-01-01T10:00:00"
    )
    assert block.service_received_at == "2026-01-01T10:00:00"


def test_live_block_rule_at_most_one_unsealed(conn):
    """The 'live block' rule: at most one sealed=0 row per conversation at any time."""
    _make_conv(conn)
    for i in range(5):
        append_block(
            conn, "conv-1", {"type": "assistant", "phase": "intermediate", "text": f"chunk {i}"}
        )
    count = conn.execute(
        "SELECT COUNT(*) FROM conversation_blocks WHERE conversation_id='conv-1' AND sealed=0"
    ).fetchone()[0]
    assert count == 1


# --- update_live_block ---


def test_update_live_block_updates_payload(conn):
    _make_conv(conn)
    seg1 = {"type": "assistant", "phase": "intermediate", "text": "hello"}
    b = append_block(conn, "conv-1", seg1)
    seg2 = {"type": "assistant", "phase": "intermediate", "text": "hello world"}
    updated = update_live_block(conn, "conv-1", seg2)
    assert updated is True
    row = conn.execute(
        "SELECT payload_json FROM conversation_blocks WHERE id=?", (b.id,)
    ).fetchone()
    assert json.loads(row["payload_json"])["text"] == "hello world"


def test_update_live_block_seals_on_terminal_kind(conn):
    """Updating the live block to a non-intermediate kind seals it in place."""
    _make_conv(conn)
    b = append_block(conn, "conv-1", {"type": "assistant", "phase": "intermediate", "text": "hi"})
    assert b.sealed is False
    update_live_block(conn, "conv-1", {"type": "assistant", "phase": "final", "text": "hi done"})
    row = conn.execute("SELECT sealed FROM conversation_blocks WHERE id=?", (b.id,)).fetchone()
    assert row["sealed"] == 1


def test_update_live_block_returns_false_if_no_live_block(conn):
    _make_conv(conn)
    seg = {"type": "user", "text": "done"}
    append_block(conn, "conv-1", seg)  # user blocks are sealed immediately
    result = update_live_block(
        conn, "conv-1", {"type": "assistant", "phase": "intermediate", "text": "?"}
    )
    assert result is False


# --- read_conversation_blocks ---


def test_read_conversation_blocks_empty(conn):
    _make_conv(conn)
    assert read_conversation_blocks(conn, "conv-1") == []


def test_read_conversation_blocks_ordered_by_ordinal(conn):
    _make_conv(conn)
    for i in range(4):
        append_block(conn, "conv-1", {"type": "user", "text": f"msg{i}"})
    blocks = read_conversation_blocks(conn, "conv-1")
    ordinals = [b.ordinal for b in blocks]
    assert ordinals == sorted(ordinals)


def test_read_conversation_blocks_returns_correct_kinds(conn):
    _make_conv(conn)
    append_block(conn, "conv-1", {"type": "user", "text": "q"})
    append_block(conn, "conv-1", {"type": "tool_call", "name": "Bash", "input": "ls"})
    append_block(conn, "conv-1", {"type": "assistant", "phase": "final", "text": "done"})
    blocks = read_conversation_blocks(conn, "conv-1")
    assert [b.kind for b in blocks] == ["user", "tool_call", "assistant_final"]


# --- read_conversation_doc ---


def test_read_conversation_doc_returns_none_for_unknown(conn):
    assert read_conversation_doc(conn, "no-such-conv") is None


def test_read_conversation_doc_has_harness_and_state(conn):
    upsert_conversation(
        conn,
        conversation_id="conv-2",
        agent_id="a",
        harness="codex",
        live_state="working",
    )
    append_block(conn, "conv-2", {"type": "user", "text": "test"})
    doc = read_conversation_doc(conn, "conv-2")
    assert doc["harness"] == "codex"
    assert doc["state"] == "working"


# --- merge_conversation_doc reconciliation ---


def test_merge_conversation_doc_longer_replaces(conn):
    """A longer parse replaces / extends stored blocks."""
    _make_conv(conn)
    short_doc = {"segments": [{"type": "user", "text": "hello"}]}
    merge_conversation_doc(conn, "conv-1", short_doc)
    assert len(read_conversation_blocks(conn, "conv-1")) == 1

    longer_doc = {
        "segments": [
            {"type": "user", "text": "hello"},
            {"type": "assistant", "phase": "final", "text": "world"},
        ]
    }
    merge_conversation_doc(conn, "conv-1", longer_doc)
    assert len(read_conversation_blocks(conn, "conv-1")) == 2


def test_merge_conversation_doc_shorter_ignored(conn):
    """A shorter parse is treated as transient noise and ignored."""
    _make_conv(conn)
    longer_doc = {
        "segments": [
            {"type": "user", "text": "q1"},
            {"type": "assistant", "phase": "final", "text": "a1"},
            {"type": "user", "text": "q2"},
        ]
    }
    merge_conversation_doc(conn, "conv-1", longer_doc)
    count_before = len(read_conversation_blocks(conn, "conv-1"))

    shorter_doc = {"segments": [{"type": "user", "text": "q1"}]}
    merge_conversation_doc(conn, "conv-1", shorter_doc)
    assert len(read_conversation_blocks(conn, "conv-1")) == count_before


def test_merge_conversation_doc_equal_count_updates_live_tail(conn):
    """Same segment count with changed last segment updates the live tail in-place."""
    _make_conv(conn)
    doc_v1 = {
        "segments": [
            {"type": "user", "text": "q"},
            {"type": "assistant", "phase": "intermediate", "text": "thinking..."},
        ]
    }
    merge_conversation_doc(conn, "conv-1", doc_v1)
    blocks_before = read_conversation_blocks(conn, "conv-1")
    assert len(blocks_before) == 2
    assert blocks_before[-1].sealed is False

    doc_v2 = {
        "segments": [
            {"type": "user", "text": "q"},
            {"type": "assistant", "phase": "intermediate", "text": "thinking... I believe..."},
        ]
    }
    merge_conversation_doc(conn, "conv-1", doc_v2)
    blocks_after = read_conversation_blocks(conn, "conv-1")
    # Still 2 blocks — updated in place, not duplicated.
    assert len(blocks_after) == 2
    assert blocks_after[-1].payload["text"] == "thinking... I believe..."
    # Same row id (updated in place)
    assert blocks_after[-1].id == blocks_before[-1].id


def test_merge_conversation_doc_equal_identical_is_noop(conn):
    """Same segment count and identical content is a no-op (no duplicates)."""
    _make_conv(conn)
    doc = {"segments": [{"type": "user", "text": "q"}]}
    merge_conversation_doc(conn, "conv-1", doc)
    merge_conversation_doc(conn, "conv-1", doc)
    assert len(read_conversation_blocks(conn, "conv-1")) == 1


def test_merge_conversation_doc_live_block_rule(conn):
    """After any merge, at most one sealed=0 block exists per conversation."""
    _make_conv(conn)
    doc = {
        "segments": [
            {"type": "user", "text": "q"},
            {"type": "tool_call", "name": "Bash"},
            {"type": "assistant", "phase": "intermediate", "text": "running..."},
        ]
    }
    merge_conversation_doc(conn, "conv-1", doc)
    count = conn.execute(
        "SELECT COUNT(*) FROM conversation_blocks WHERE conversation_id='conv-1' AND sealed=0"
    ).fetchone()[0]
    assert count <= 1


def test_merge_conversation_doc_equal_count_flip_to_final_seals(conn):
    """An equal-count merge that flips the live tail to phase=final seals it.

    The live-block rule says a block seals when its phase flips to final, so a
    finished turn never lingers as a mutable tail (which would keep emitting
    block-updated events in 1.d). Regression for update_live_block leaving the
    block sealed=0 on the intermediate→final transition.
    """
    _make_conv(conn)
    merge_conversation_doc(
        conn,
        "conv-1",
        {
            "segments": [
                {"type": "user", "text": "q"},
                {"type": "assistant", "phase": "intermediate", "text": "thinking..."},
            ]
        },
    )
    assert read_conversation_blocks(conn, "conv-1")[-1].sealed is False

    merge_conversation_doc(
        conn,
        "conv-1",
        {
            "segments": [
                {"type": "user", "text": "q"},
                {"type": "assistant", "phase": "final", "text": "done"},
            ]
        },
    )
    tail = read_conversation_blocks(conn, "conv-1")[-1]
    assert tail.kind == "assistant_final"
    assert tail.sealed is True


def test_merge_conversation_doc_updates_conversation_metadata(conn):
    """merge_conversation_doc refreshes harness/state/condensed on the conversations row."""
    _make_conv(conn)
    doc = {
        "harness": "codex",
        "state": "awaiting_input",
        "condensed": "short summary",
        "segments": [{"type": "user", "text": "x"}],
    }
    merge_conversation_doc(conn, "conv-1", doc)
    row = conn.execute(
        "SELECT harness, live_state, condensed FROM conversations WHERE conversation_id='conv-1'"
    ).fetchone()
    assert row["harness"] == "codex"
    assert row["live_state"] == "awaiting_input"
    assert row["condensed"] == "short summary"


def test_merge_conversation_doc_timestamps_stored(conn):
    """service_received_at is stored on each block."""
    _make_conv(conn)
    ts = "2026-01-01T12:00:00"
    doc = {"segments": [{"type": "user", "text": "t"}]}
    merge_conversation_doc(conn, "conv-1", doc, received_at=ts)
    blocks = read_conversation_blocks(conn, "conv-1")
    assert blocks[0].service_received_at == ts


# --- mark_stale_conversations ---


def test_mark_stale_conversations_flips_in_progress(conn):
    """Startup reconciliation flips in_progress rows to stale."""
    for i in range(3):
        upsert_conversation(conn, conversation_id=f"conv-{i}", agent_id="a", status="in_progress")
    count = mark_stale_conversations(conn)
    assert count == 3
    rows = conn.execute("SELECT status FROM conversations").fetchall()
    assert all(r["status"] == "stale" for r in rows)


def test_mark_stale_conversations_leaves_complete_alone(conn):
    """complete rows must not be touched by startup reconciliation."""
    upsert_conversation(conn, conversation_id="conv-done", agent_id="a", status="complete")
    mark_stale_conversations(conn)
    row = conn.execute(
        "SELECT status FROM conversations WHERE conversation_id='conv-done'"
    ).fetchone()
    assert row["status"] == "complete"


def test_mark_stale_conversations_returns_zero_when_nothing_to_do(conn):
    upsert_conversation(conn, conversation_id="conv-x", agent_id="a", status="stale")
    count = mark_stale_conversations(conn)
    assert count == 0


# --- init_db idempotency ---


def test_init_db_idempotent(conn):
    """Calling init_db twice on an existing DB must not raise and must not add tables."""
    tables_before = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    init_db(conn)  # second call
    tables_after = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert tables_before == tables_after


def test_init_db_creates_conversations_table(conn):
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "conversations" in tables
    assert "conversation_blocks" in tables


# --- isolation: agent_messages unaffected ---


def test_agent_messages_unaffected_by_conversation_store(conn):
    """Inserting conversation blocks must not touch agent_messages."""
    conn.execute(
        "INSERT INTO agent_messages(agent_id, ordinal, role, body, captured_at)"
        " VALUES ('agent-1', 0, 'user', 'hello', '2026-01-01T00:00:00')"
    )
    _make_conv(conn)
    append_block(conn, "conv-1", {"type": "user", "text": "hi"})
    rows = conn.execute("SELECT body FROM agent_messages WHERE agent_id='agent-1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["body"] == "hello"


# --- live choice_prompt blocks (chat-input takeover) ---


def _choice_seg(*, selected: int = 1, answered: bool = False, checked: list[int] | None = None):
    return {
        "type": "choice_prompt",
        "question": "Which?",
        "options": [
            {"number": 1, "label": "A", "description": None, "checked": (checked is not None and 1 in checked) if checked is not None else None},
            {"number": 2, "label": "B", "description": None, "checked": (checked is not None and 2 in checked) if checked is not None else None},
        ],
        "footer": None,
        "selected": selected,
        "answered": answered,
        "chosen": None,
        "multi": checked is not None,
    }


def test_unanswered_choice_prompt_stays_live_and_updates_in_place(conn):
    """Cursor/checkbox moves must reach the TUI as block-updated events: an
    unanswered choice_prompt is a LIVE block (sealed=0) and merge updates it."""
    from murder.state.persistence.conversation import merge_non_user_segments_with_changes

    _make_conv(conn)
    block = append_block(conn, "conv-1", _choice_seg(selected=1))
    assert block.sealed is False

    _, changes = merge_non_user_segments_with_changes(conn, "conv-1", [_choice_seg(selected=2)])
    assert [(c.action, c.block.kind) for c in changes] == [("block-updated", "choice_prompt")]
    assert changes[0].block.payload["selected"] == 2
    assert changes[0].block.sealed is False


def test_answered_choice_prompt_seals_on_update(conn):
    """Resolution (answered=True) seals the block in place — no longer mutable."""
    from murder.state.persistence.conversation import merge_non_user_segments_with_changes

    _make_conv(conn)
    append_block(conn, "conv-1", _choice_seg(selected=1))
    resolved = _choice_seg(selected=1, answered=True)
    resolved["chosen"] = 1
    _, changes = merge_non_user_segments_with_changes(conn, "conv-1", [resolved])
    assert changes and changes[0].action == "block-updated"
    row = conn.execute(
        "SELECT sealed FROM conversation_blocks WHERE conversation_id='conv-1'"
    ).fetchone()
    assert row["sealed"] == 1


def test_answered_choice_prompt_seals_at_insert(conn):
    _make_conv(conn)
    block = append_block(conn, "conv-1", _choice_seg(selected=1, answered=True))
    assert block.sealed is True
