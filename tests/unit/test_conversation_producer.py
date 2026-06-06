"""ConversationProducer — portable projection unit tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder.runtime.agents.conversation_producer import ConversationProducer
from murder.state.persistence.conversation import read_conversation_blocks, read_conversation_doc
from murder.state.persistence.schema import get_db, init_db

_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cc" / "frames"
_CODEX_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "codex" / "frames"
_PI_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "pi" / "frames"
_AGY_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "antigravity" / "frames"
_CURSOR_FRAMES_DIR = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cursor" / "frames"


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
) -> ConversationProducer:
    async def publish(action: str, block: dict[str, Any]) -> None:
        published.append((action, block))

    return ConversationProducer(
        conversation_id=conversation_id,
        harness_kind=harness_kind,
        system_prompt=system_prompt,
        db=conn,
        publish=publish,
    )


def _load_frame(n: int) -> str:
    return (_FRAMES_DIR / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


def _load_codex_frame(n: int) -> str:
    return (_CODEX_FRAMES_DIR / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


def _load_pi_frame(n: int) -> str:
    return (_PI_FRAMES_DIR / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


def _load_agy_frame(n: int) -> str:
    return (_AGY_FRAMES_DIR / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


def _load_cursor_frame(n: int) -> str:
    return (_CURSOR_FRAMES_DIR / f"{n:04d}.txt").read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------


def test_poll_persists_assistant_block_and_sets_harness(conn: sqlite3.Connection) -> None:
    """A frame containing an assistant reply produces a persisted block + event."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published)

    import asyncio

    # Feed enough frames to get a complete transcript
    for i in range(len(list(_FRAMES_DIR.iterdir()))):
        frame = _load_frame(i)
        asyncio.run(producer.poll(frame))

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

    pane = _load_frame(50)
    asyncio.run(producer.poll(pane))
    after_first = len(published)

    # Second poll of the identical pane must not emit any new events.
    asyncio.run(producer.poll(pane))
    assert len(published) == after_first


def test_poll_growing_pane_appends(conn: sqlite3.Connection) -> None:
    """Feeding a later frame that extends the transcript appends new blocks."""
    published: list[tuple[str, dict[str, Any]]] = []
    producer = _make_producer(conn, published)

    import asyncio

    # Start with an early frame that has fewer segments.
    asyncio.run(producer.poll(_load_frame(20)))
    blocks_after_early = read_conversation_blocks(conn, "crow-t001")

    # Feed a later frame; the parser should see more content.
    asyncio.run(producer.poll(_load_frame(80)))
    blocks_after_late = read_conversation_blocks(conn, "crow-t001")

    # The store should not have shrunk.
    assert len(blocks_after_late) >= len(blocks_after_early)


def test_per_frame_accumulation_invariants(conn: sqlite3.Connection) -> None:
    """Feed cc fixture frames one at a time; assert DB invariants at every step.

    Catches regressions where the system prompt leaks into conversation blocks
    or where monotonicity breaks (blocks disappear between frames).
    """
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(conn, published, system_prompt=system_prompt)

    import asyncio

    frame_count = len(list(_FRAMES_DIR.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_frame(i)))

        blocks = read_conversation_blocks(conn, "crow-t001")

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


def test_per_frame_accumulation_invariants_codex(conn: sqlite3.Connection) -> None:
    """Feed codex fixture frames one at a time; assert DB invariants at every step."""
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(
        conn, published,
        conversation_id="codex-t001",
        harness_kind="codex",
        system_prompt=system_prompt,
    )

    import asyncio

    frame_count = len(list(_CODEX_FRAMES_DIR.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_codex_frame(i)))

        blocks = read_conversation_blocks(conn, "codex-t001")

        assert len(blocks) >= prev_block_count, (
            f"block count shrank at frame {i}: {prev_block_count} → {len(blocks)}"
        )
        prev_block_count = len(blocks)

        kinds = {b.kind for b in blocks}
        assert "user" not in kinds, f"frame {i}: parser wrote a user block (should be stripped)"

        for block in blocks:
            payload_str = str(block.payload)
            for fragment in ("You are a collaborator", "Please help the user"):
                assert fragment not in payload_str, (
                    f"frame {i}: system prompt fragment {fragment!r} found in block {block.kind}"
                )


def test_per_frame_accumulation_invariants_pi(conn: sqlite3.Connection) -> None:
    """Feed pi fixture frames one at a time; assert DB invariants at every step."""
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(
        conn, published,
        conversation_id="pi-t001",
        harness_kind="pi",
        system_prompt=system_prompt,
    )

    import asyncio

    frame_count = len(list(_PI_FRAMES_DIR.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_pi_frame(i)))

        blocks = read_conversation_blocks(conn, "pi-t001")

        assert len(blocks) >= prev_block_count, (
            f"block count shrank at frame {i}: {prev_block_count} → {len(blocks)}"
        )
        prev_block_count = len(blocks)

        kinds = {b.kind for b in blocks}
        assert "user" not in kinds, f"frame {i}: parser wrote a user block (should be stripped)"

        for block in blocks:
            payload_str = str(block.payload)
            for fragment in ("You are a collaborator", "Please help the user"):
                assert fragment not in payload_str, (
                    f"frame {i}: system prompt fragment {fragment!r} found in block {block.kind}"
                )


def test_per_frame_accumulation_invariants_antigravity(conn: sqlite3.Connection) -> None:
    """Feed antigravity fixture frames one at a time; assert DB invariants at every step."""
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(
        conn, published,
        conversation_id="agy-t001",
        harness_kind="antigravity",
        system_prompt=system_prompt,
    )

    import asyncio

    frame_count = len(list(_AGY_FRAMES_DIR.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_agy_frame(i)))

        blocks = read_conversation_blocks(conn, "agy-t001")

        assert len(blocks) >= prev_block_count, (
            f"block count shrank at frame {i}: {prev_block_count} → {len(blocks)}"
        )
        prev_block_count = len(blocks)

        kinds = {b.kind for b in blocks}
        assert "user" not in kinds, f"frame {i}: parser wrote a user block (should be stripped)"

        for block in blocks:
            payload_str = str(block.payload)
            for fragment in ("You are a collaborator", "Please help the user"):
                assert fragment not in payload_str, (
                    f"frame {i}: system prompt fragment {fragment!r} found in block {block.kind}"
                )


def test_per_frame_accumulation_invariants_cursor(conn: sqlite3.Connection) -> None:
    """Feed cursor fixture frames one at a time; assert DB invariants at every step."""
    published: list[tuple[str, dict[str, Any]]] = []
    system_prompt = "You are a collaborator.\n\nPlease help the user with their request."
    producer = _make_producer(
        conn, published,
        conversation_id="cursor-t001",
        harness_kind="cursor",
        system_prompt=system_prompt,
    )

    import asyncio

    frame_count = len(list(_CURSOR_FRAMES_DIR.iterdir()))
    prev_block_count = 0
    for i in range(frame_count):
        asyncio.run(producer.poll(_load_cursor_frame(i)))

        blocks = read_conversation_blocks(conn, "cursor-t001")

        assert len(blocks) >= prev_block_count, (
            f"block count shrank at frame {i}: {prev_block_count} → {len(blocks)}"
        )
        prev_block_count = len(blocks)

        kinds = {b.kind for b in blocks}
        assert "user" not in kinds, f"frame {i}: parser wrote a user block (should be stripped)"

        for block in blocks:
            payload_str = str(block.payload)
            for fragment in ("You are a collaborator", "Please help the user"):
                assert fragment not in payload_str, (
                    f"frame {i}: system prompt fragment {fragment!r} found in block {block.kind}"
                )


def test_poll_different_conversation_ids_are_isolated(conn: sqlite3.Connection) -> None:
    """Two producers with different conversation_ids write to separate stores."""
    published_a: list[tuple[str, dict[str, Any]]] = []
    published_b: list[tuple[str, dict[str, Any]]] = []
    prod_a = _make_producer(conn, published_a, conversation_id="crow-a001")
    prod_b = _make_producer(conn, published_b, conversation_id="crow-b001")

    import asyncio

    pane = _load_frame(50)
    asyncio.run(prod_a.poll(pane))
    asyncio.run(prod_b.poll(pane))

    blocks_a = read_conversation_blocks(conn, "crow-a001")
    blocks_b = read_conversation_blocks(conn, "crow-b001")
    assert all(b.conversation_id == "crow-a001" for b in blocks_a)
    assert all(b.conversation_id == "crow-b001" for b in blocks_b)
    assert len(published_a) > 0
    assert len(published_b) > 0
