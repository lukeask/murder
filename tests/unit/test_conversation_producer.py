"""Tests for murder.runtime.agents.conversation_producer.

COOKBOOK = hash-skip noop (same pane twice → no new events) + growing-pane
           append (later frame adds blocks) — the two most common caller patterns.
EDGE CASES = per-frame monotonic accumulation invariants across all harness
             fixture sets; conversation-id isolation between concurrent producers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder.runtime.agents.conversation_producer import ConversationProducer
from murder.state.persistence.conversation import read_conversation_blocks, read_conversation_doc
from murder.state.persistence.schema import get_db, init_db

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
_FRAMES_DIR = _FIXTURES / "cc" / "frames"
_CODEX_FRAMES_DIR = _FIXTURES / "codex" / "frames"
_PI_FRAMES_DIR = _FIXTURES / "pi" / "frames"
_AGY_FRAMES_DIR = _FIXTURES / "antigravity" / "frames"
_CURSOR_FRAMES_DIR = _FIXTURES / "cursor" / "frames"


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = get_db(tmp_path / "test.db")
    init_db(db)
    return db


def _make_producer(
    conn: sqlite3.Connection,
    published: list[tuple[str, dict[str, Any]]],
    *,
    conversation_id: str = "crow-t001",
    harness_kind: str = "claude_code",
    system_prompt: str | None = None,
    summary_provider: Any = None,
) -> ConversationProducer:
    async def publish(action: str, block: dict[str, Any]) -> None:
        published.append((action, block))

    return ConversationProducer(
        conversation_id=conversation_id,
        harness_kind=harness_kind,
        system_prompt=system_prompt,
        db=conn,
        publish=publish,
        summary_provider=summary_provider,
    )


def _load_frame(frames_dir: Path, n: int) -> str:
    return (frames_dir / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_poll_persists_assistant_block_and_sets_harness(conn: sqlite3.Connection) -> None:
    """A frame containing an assistant reply produces a persisted block + event."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published)

    import asyncio

    # Feed enough frames to get a complete transcript
    for i in range(len(list(_FRAMES_DIR.iterdir()))):
        asyncio.run(producer.poll(_load_frame(_FRAMES_DIR, i)))

    blocks = read_conversation_blocks(conn, "crow-t001")
    kinds = {b.kind for b in blocks}
    assert "assistant_final" in kinds or "assistant_intermediate" in kinds

    doc = read_conversation_doc(conn, "crow-t001")
    assert doc is not None
    assert doc["harness"] == "claude_code"

    # At least one event was published for each new block
    assert len(published) > 0
    assert all(action in ("block-appended", "block-updated") for action, _ in published)


def test_poll_hash_skip_is_noop(conn: sqlite3.Connection) -> None:
    """Polling the same pane twice produces no new events on the second call."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published)

    import asyncio

    pane = _load_frame(_FRAMES_DIR, 50)
    asyncio.run(producer.poll(pane))
    after_first = len(published)

    # Second poll of the identical pane must not emit any new events.
    asyncio.run(producer.poll(pane))
    assert len(published) == after_first


def test_document_identity_is_content_addressed_not_capture_provenance(
    conn: sqlite3.Connection,
) -> None:
    """The same verified document from another capture is a true producer no-op."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published, conversation_id="agent-content-hash")
    producer._summary_provider_resolved = True
    doc = {
        "harness": "claude_code",
        "state": "awaiting_input",
        "condensed": None,
        "segments": [{"type": "assistant", "phase": "final", "text": "same reply"}],
    }

    import asyncio

    first = asyncio.run(producer.poll_document(doc))
    captured_at = conn.execute(
        "SELECT captured_at FROM agent_messages WHERE agent_id = ?", ("agent-content-hash",)
    ).fetchone()["captured_at"]
    second = asyncio.run(producer.poll_document(dict(doc)))

    assert first.changed
    assert not second.changed
    assert second.changes == ()
    assert len(published) == 1
    assert conn.execute(
        "SELECT captured_at FROM agent_messages WHERE agent_id = ?", ("agent-content-hash",)
    ).fetchone()["captured_at"] == captured_at


def test_poll_growing_pane_appends(conn: sqlite3.Connection) -> None:
    """Feeding a later frame that extends the transcript appends new blocks."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published)

    import asyncio

    # Start with an early frame that has fewer segments.
    asyncio.run(producer.poll(_load_frame(_FRAMES_DIR, 20)))
    blocks_after_early = read_conversation_blocks(conn, "crow-t001")

    # Feed a later frame; the parser should see more content.
    asyncio.run(producer.poll(_load_frame(_FRAMES_DIR, 80)))
    blocks_after_late = read_conversation_blocks(conn, "crow-t001")

    # The store should not have shrunk.
    assert len(blocks_after_late) >= len(blocks_after_early)


# ============================================================
# === EDGE CASES =============================================
# ============================================================


@pytest.mark.parametrize(
    "harness_kind, conversation_id, frames_dir",
    [
        ("claude_code", "crow-t001", _FRAMES_DIR),
        ("codex", "codex-t001", _CODEX_FRAMES_DIR),
        ("pi", "pi-t001", _PI_FRAMES_DIR),
        ("antigravity", "agy-t001", _AGY_FRAMES_DIR),
        ("cursor", "cursor-t001", _CURSOR_FRAMES_DIR),
    ],
    ids=["cc", "codex", "pi", "antigravity", "cursor"],
)
def test_per_frame_accumulation_invariants(
    conn: sqlite3.Connection,
    harness_kind: str,
    conversation_id: str,
    frames_dir: Path,
) -> None:
    """Feed fixture frames one at a time; assert DB invariants at every step.

    Catches regressions where the system prompt leaks into conversation blocks
    or where monotonicity breaks (blocks disappear between frames).
    """
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(
        conn,
        published,
        conversation_id=conversation_id,
        harness_kind=harness_kind,
        system_prompt=system_prompt,
    )

    import asyncio

    frame_count = len(list(frames_dir.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_frame(frames_dir, i)))

        blocks = read_conversation_blocks(conn, conversation_id)

        # Blocks are monotonically non-decreasing (no block disappears mid-session).
        assert len(blocks) >= prev_block_count, (
            f"block count shrank at frame {i}: {prev_block_count} → {len(blocks)}"
        )
        prev_block_count = len(blocks)

        # The parser strips user segments, so poll() alone never writes user blocks.
        # Ground-truth user blocks only come from record_user_block_event().
        kinds = {b.kind for b in blocks}
        assert "user" not in kinds, f"frame {i}: parser wrote a user block (should be stripped)"

        # System prompt text must never appear in any stored block payload.
        for block in blocks:
            payload_str = str(block.payload)
            for fragment in ("You are a collaborator", "Please help the user"):
                assert fragment not in payload_str, (
                    f"frame {i}: system prompt fragment {fragment!r} found in block {block.kind}"
                )


def test_poll_summarizes_off_hot_path_into_chunk_storage(conn: sqlite3.Connection) -> None:
    """A sealed intermediate run past the char threshold lands a chunk summary.

    The summary call is dispatched via create_task (off the hot path); poll()
    itself never awaits the provider. We assert the provider WAS called and the
    chunk landed in conversation_chunk_summaries with explicit block-id
    attribution, draining the background task at the end.
    """
    import asyncio

    from murder.state.persistence.conversation import (
        read_chunk_summaries,
        read_conversation_blocks,
    )

    class _StubProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def summarize(self, prompt: Any) -> str:
            self.calls += 1
            return "Agent worked through intermediate steps."

    provider = _StubProvider()
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published, summary_provider=provider)
    # Force a low threshold so a realistic transcript trips a flush.
    producer._summary_buffer.char_threshold = 50

    async def drive() -> None:
        frame_count = len(list(_FRAMES_DIR.iterdir()))
        for i in range(frame_count):
            await producer.poll(_load_frame(_FRAMES_DIR, i))
        # Drain any in-flight background summary tasks.
        if producer._summary_tasks:
            await asyncio.gather(*list(producer._summary_tasks))

    asyncio.run(drive())

    blocks = read_conversation_blocks(conn, "crow-t001")
    has_intermediate = any(b.kind == "assistant_intermediate" for b in blocks)
    summaries = read_chunk_summaries(conn, "crow-t001")
    if has_intermediate:
        # If the fixture produced sealed intermediate content, we summarized it.
        assert provider.calls >= 1
        assert summaries, "expected at least one chunk summary"
        valid_ids = {b.id for b in blocks}
        for s in summaries:
            assert s.summary == "Agent worked through intermediate steps."
            # Attribution points at real block ids (the contract).
            assert all(bid in valid_ids for bid in s.block_ids)


def test_short_turn_flushes_chunk_on_working_to_idle_boundary(
    conn: sqlite3.Connection,
) -> None:
    """A short turn below the rolling char threshold still yields a chunk summary.

    Regression: the rolling buffer only flushes mid-stream when its running
    char-sum crosses the threshold. A short turn (read a couple files, write
    one, then reply) weighs only ~100 weighted chars and so never trips a
    mid-turn flush. Without a turn-boundary flush its buffered intermediate run
    sits forever and Condensed renders identically to Verbose. We assert that
    the working→idle transition force-flushes the tail so the turn produces at
    least one summary even with the *production* threshold left high.
    """
    import asyncio

    from murder.state.persistence.conversation import (
        read_chunk_summaries,
        read_conversation_blocks,
    )

    class _StubProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def summarize(self, prompt: Any) -> str:
            self.calls += 1
            return "Short turn summary."

    provider = _StubProvider()
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published, summary_provider=provider)
    # Keep the threshold high so the ONLY way a flush can happen is the
    # turn-boundary completion flush (not the rolling mid-turn flush).
    producer._summary_buffer.char_threshold = 100_000

    async def drive() -> None:
        frame_count = len(list(_FRAMES_DIR.iterdir()))
        for i in range(frame_count):
            await producer.poll(_load_frame(_FRAMES_DIR, i))
        if producer._summary_tasks:
            await asyncio.gather(*list(producer._summary_tasks))

    asyncio.run(drive())

    blocks = read_conversation_blocks(conn, "crow-t001")
    has_intermediate = any(b.kind == "assistant_intermediate" for b in blocks)
    summaries = read_chunk_summaries(conn, "crow-t001")
    assert has_intermediate, "cc fixture must produce intermediate blocks for this test"
    # The cc fixture ends working→awaiting_input, so the boundary flush fires.
    assert provider.calls >= 1
    assert summaries, "completion flush should produce a tail chunk summary"
    valid_ids = {b.id for b in blocks}
    for s in summaries:
        assert s.summary == "Short turn summary."
        assert all(bid in valid_ids for bid in s.block_ids)

    # Regression: EVERY summarizable intermediate block (assistant prose AND tool
    # calls) must be attributed to a summary — otherwise Condensed renders it
    # verbatim. The seal-on-supersede transition for streaming
    # ``assistant_intermediate`` blocks used to be invisible, so their prose was
    # never buffered/summarized and Condensed looked identical to Verbose.
    covered = {bid for s in summaries for bid in s.block_ids}
    summarizable = {
        b.id for b in blocks if b.kind in ("assistant_intermediate", "tool_call")
    }
    assert summarizable - covered == set(), (
        "every intermediate (prose + tool) block must be attributed; "
        f"uncovered={sorted(summarizable - covered)}"
    )
    # The verbatim final reply must NEVER be attributed to a summary.
    finals = {b.id for b in blocks if b.kind == "assistant_final"}
    assert finals & covered == set(), "final reply must stay verbatim (never summarized)"


def test_chunk_summarized_publish_constructs_a_valid_bus_event(
    conn: sqlite3.Connection,
) -> None:
    """The producer's ``chunk-summarized`` publish must build a valid ConversationBlockEvent.

    LIVE-failure regression. Every other producer test stubs ``publish`` with a
    plain list-append, so they never exercise the real bus-event construction.
    In production ``publish`` is ``AgentBase._publish_conversation_block``, which
    constructs a ``ConversationBlockEvent``. That model's ``action`` Literal
    originally allowed only ``block-appended``/``block-updated``; the producer's
    third action ``chunk-summarized`` raised a pydantic ValidationError, the
    event never reached the client, and Condensed rendered identically to
    Verbose even though the chunk summary was written to the DB. This test wires
    ``publish`` through the SAME model so a regressing Literal fails here.
    """
    import asyncio

    from murder.runtime.orchestration.events import ConversationBlockEvent

    class _StubProvider:
        async def summarize(self, prompt: Any) -> str:
            return "Short turn summary."

    events: list[ConversationBlockEvent] = []

    async def publish_via_real_event(action: str, block: dict[str, Any]) -> None:
        # Mirror AgentBase._publish_conversation_block: any action the producer
        # emits must be accepted by the wire model, or the publish raises.
        events.append(
            ConversationBlockEvent(
                run_id="run-t001",
                agent_id="crow-t001",
                role="crow",
                ticket_id=None,
                conversation_id="crow-t001",
                action=action,  # type: ignore[arg-type]
                block=block,
            )
        )

    producer = ConversationProducer(
        conversation_id="crow-t001",
        harness_kind="claude_code",
        system_prompt=None,
        db=conn,
        publish=publish_via_real_event,
        summary_provider=_StubProvider(),
    )
    # High threshold so the only flush is the working→idle completion flush.
    producer._summary_buffer.char_threshold = 100_000

    async def drive() -> None:
        frame_count = len(list(_FRAMES_DIR.iterdir()))
        for i in range(frame_count):
            await producer.poll(_load_frame(_FRAMES_DIR, i))
        if producer._summary_tasks:
            await asyncio.gather(*list(producer._summary_tasks))

    asyncio.run(drive())

    actions = {e.action for e in events}
    assert "chunk-summarized" in actions, (
        "producer must publish a chunk-summarized event the wire model accepts; "
        f"saw actions={sorted(actions)}"
    )
    summary_events = [e for e in events if e.action == "chunk-summarized"]
    for ev in summary_events:
        assert ev.block.get("summary"), "chunk-summarized block must carry summary text"
        assert "block_ids" in ev.block


def test_poll_different_conversation_ids_are_isolated(conn: sqlite3.Connection) -> None:
    """Two producers with different conversation_ids write to separate stores."""
    published_a: list[tuple[str, dict[str, Any]]] = []
    published_b: list[tuple[str, dict[str, Any]]] = []
    prod_a = _make_producer(conn, published_a, conversation_id="crow-a001")
    prod_b = _make_producer(conn, published_b, conversation_id="crow-b001")

    import asyncio

    pane = _load_frame(_FRAMES_DIR, 50)
    asyncio.run(prod_a.poll(pane))
    asyncio.run(prod_b.poll(pane))

    blocks_a = read_conversation_blocks(conn, "crow-a001")
    blocks_b = read_conversation_blocks(conn, "crow-b001")
    assert all(b.conversation_id == "crow-a001" for b in blocks_a)
    assert all(b.conversation_id == "crow-b001" for b in blocks_b)
    assert len(published_a) > 0
    assert len(published_b) > 0
