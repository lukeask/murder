"""Tests for murder.app.tui.stores.conversations.

COOKBOOK = bootstrap + apply_event roundtrip (canonical usage); copyable by
           store consumers and integration tests.
EDGE CASES = no-op duplicate block (snapshot identity + no notification),
             query helpers, snapshot ordering, no Textual import boundary.
"""

from __future__ import annotations

import types

import pytest

from murder.app.tui.stores.conversations import ConversationsStore
from tests.support.factories import (
    factory_conversation_block,
    factory_conversation_summary,
    factory_conversations_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream_event(
    conversation_id: str,
    ordinal: int,
    payload: dict | None = None,
    block_id: int | None = None,
    agent_id: str = "",
) -> object:
    block = {
        "id": block_id,
        "ordinal": ordinal,
        "kind": "user",
        "payload": payload or {"type": "user", "text": f"msg-{ordinal}"},
        "sealed": True,
        "service_received_at": "2026-01-01T00:00:00",
    }
    return types.SimpleNamespace(
        conversation_id=conversation_id,
        block=block,
        agent_id=agent_id,
    )


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_bootstrap_empty_snapshot_produces_empty_snapshot() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    assert store.get_snapshot().conversations == ()


def test_bootstrap_populates_conversations() -> None:
    store = ConversationsStore()
    summary = factory_conversation_summary(blocks=(factory_conversation_block(0),))
    store.bootstrap(factory_conversations_snapshot(summary))
    snap = store.get_snapshot()
    assert len(snap.conversations) == 1
    assert snap.conversations[0].conversation_id == "conv-1"


def test_bootstrap_notifies_subscribers() -> None:
    store = ConversationsStore()
    calls: list[int] = []
    store.subscribe(lambda: calls.append(1))
    store.bootstrap(factory_conversations_snapshot(factory_conversation_summary()))
    assert calls == [1]


def test_apply_event_after_bootstrap_updates_snapshot() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    snap_before = store.get_snapshot()

    event = _stream_event("conv-new", ordinal=0, payload={"type": "user", "text": "hello"})
    store.apply_event(event)

    snap_after = store.get_snapshot()
    assert snap_after is not snap_before
    assert len(snap_after.conversations) == 1
    assert snap_after.conversations[0].conversation_id == "conv-new"


def test_apply_event_notifies_subscribers() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    calls: list[int] = []
    store.subscribe(lambda: calls.append(1))

    store.apply_event(_stream_event("conv-1", ordinal=0))
    assert calls == [1]


@pytest.mark.parametrize(
    "event_kwargs, expected_result",
    [
        (
            {"conversation_id": "conv-abc", "ordinal": 0},
            "conv-abc",
        ),
        (
            {},  # malformed — no conversation_id attribute
            None,
        ),
    ],
    ids=["valid-event-returns-conversation-id", "invalid-event-returns-none"],
)
def test_apply_event_return_value_contract(event_kwargs: dict, expected_result: object) -> None:
    """apply_event returns the conversation_id on success, None on malformed input."""
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())

    if event_kwargs:
        event = _stream_event(**event_kwargs)
    else:
        event = types.SimpleNamespace()  # no conversation_id

    result = store.apply_event(event)
    assert result == expected_result


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_bootstrap_snapshot_is_frozen() -> None:
    store = ConversationsStore()
    store.bootstrap(
        factory_conversations_snapshot(
            factory_conversation_summary(blocks=(factory_conversation_block(0),))
        )
    )
    snap = store.get_snapshot()
    assert isinstance(snap.conversations, tuple)
    with pytest.raises((TypeError, AttributeError)):
        snap.conversations = ()  # type: ignore[misc]


def test_duplicate_block_preserves_snapshot_identity() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    event = _stream_event("conv-1", ordinal=0, block_id=42, payload={"type": "user", "text": "hi"})
    store.apply_event(event)
    snap_after_first = store.get_snapshot()

    # Same event again
    store.apply_event(event)
    snap_after_second = store.get_snapshot()

    assert snap_after_second is snap_after_first


def test_duplicate_block_does_not_notify() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    event = _stream_event("conv-1", ordinal=0, block_id=42, payload={"type": "user", "text": "hi"})
    store.apply_event(event)

    calls: list[int] = []
    store.subscribe(lambda: calls.append(1))

    store.apply_event(event)
    assert calls == []


def test_conversation_for_returns_render_conversation() -> None:
    store = ConversationsStore()
    summary = factory_conversation_summary(
        blocks=(factory_conversation_block(0, payload={"type": "user", "text": "q"}),)
    )
    store.bootstrap(factory_conversations_snapshot(summary))

    conv = store.conversation_for("conv-1")
    assert conv is not None
    assert conv.conversation_id == "conv-1"
    assert len(conv.segments) == 1


def test_conversation_for_unknown_returns_none() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    assert store.conversation_for("no-such") is None


def test_doc_for_returns_doc() -> None:
    store = ConversationsStore()
    summary = factory_conversation_summary(blocks=(factory_conversation_block(0),))
    store.bootstrap(factory_conversations_snapshot(summary))

    doc = store.doc_for("conv-1")
    assert doc is not None
    assert "segments" in doc


def test_conversation_id_for_agent_via_bootstrap() -> None:
    store = ConversationsStore()
    summary = factory_conversation_summary(conversation_id="conv-1", agent_id="agent-abc")
    store.bootstrap(factory_conversations_snapshot(summary))

    assert store.conversation_id_for_agent("agent-abc") == "conv-1"


def test_conversation_id_for_agent_via_stream_event() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    event = _stream_event("conv-99", ordinal=0, agent_id="crow-xyz")
    store.apply_event(event)

    assert store.conversation_id_for_agent("crow-xyz") == "conv-99"


def test_conversation_id_for_agent_prefix() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    event = _stream_event("conv-99", ordinal=0, agent_id="crow-t001-main")
    store.apply_event(event)

    assert store.conversation_id_for_agent_prefix("crow-t001") == "conv-99"


def test_conversation_id_for_agent_prefix_unknown_returns_none() -> None:
    store = ConversationsStore()
    store.bootstrap(factory_conversations_snapshot())
    assert store.conversation_id_for_agent_prefix("no-such") is None


def test_snapshot_conversations_sorted_by_id() -> None:
    store = ConversationsStore()
    s1 = factory_conversation_summary("conv-zzz", "agent-1")
    s2 = factory_conversation_summary("conv-aaa", "agent-2")
    store.bootstrap(factory_conversations_snapshot(s1, s2))

    ids = [c.conversation_id for c in store.get_snapshot().conversations]
    assert ids == sorted(ids)


def test_no_textual_import_in_store_module() -> None:
    import re
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent
        / "murder"
        / "app"
        / "tui"
        / "stores"
        / "conversations.py"
    ).read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)
