"""TUIchat Phase 4 — condensed backend: buffer, summarizer, storage tests."""

from __future__ import annotations

import asyncio

import pytest

from murder.llm.harnesses.transcript_summarize import (
    SummaryPrompt,
    is_final_segment,
    summarize_chunk,
    tool_call_descriptor,
)
from murder.runtime.agents.summarization_buffer import (
    DEFAULT_CHAR_THRESHOLD,
    SummarizationBuffer,
)
from murder.state.persistence import conversation as conv_store
from murder.state.persistence.schema import init_db


# --------------------------------------------------------------------------
# Summarizer-to-spec
# --------------------------------------------------------------------------

class _StubProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[SummaryPrompt] = []

    async def summarize(self, prompt: SummaryPrompt) -> str:
        self.prompts.append(prompt)
        return self.response


def test_tool_call_descriptor_strips_payload() -> None:
    seg = {
        "type": "tool_call",
        "title": "edit murder/foo.py",
        "input": "<<huge diff payload>>",
        "result": "<<huge result>>",
        "elided": False,
        "running": False,
    }
    desc = tool_call_descriptor(seg)
    assert "edited" in desc
    assert "murder/foo.py" in desc
    assert "completed" in desc
    assert "huge" not in desc  # payload never leaks


def test_tool_call_descriptor_running_status() -> None:
    seg = {"type": "tool_call", "title": "pytest -q", "running": True}
    assert "running" in tool_call_descriptor(seg)
    assert tool_call_descriptor(seg).startswith("ran")


def test_final_never_summarized() -> None:
    final = {"type": "assistant", "phase": "final", "text": "All done."}
    inter = {"type": "assistant", "phase": "intermediate", "text": "Working..."}
    assert is_final_segment(final) is True
    assert is_final_segment(inter) is False

    provider = _StubProvider("ignored")
    # A chunk that is ONLY a final reply yields None and never calls the provider.
    out = asyncio.run(summarize_chunk(segments=[final], provider=provider))
    assert out is None
    assert provider.prompts == []


def test_final_dropped_from_mixed_chunk() -> None:
    provider = _StubProvider("Agent edited the parser.")
    out = asyncio.run(
        summarize_chunk(
            segments=[
                {"type": "assistant", "phase": "intermediate", "text": "Looking at parser."},
                {"type": "assistant", "phase": "final", "text": "Done, verbatim."},
            ],
            provider=provider,
        )
    )
    assert out == "Agent edited the parser."
    assert len(provider.prompts) == 1
    # The final segment must not appear in the prompt payload.
    assert "Done, verbatim." not in provider.prompts[0].user


def test_empty_summary_falls_back_to_none() -> None:
    """Latent-bug fix: a blank provider response degrades to None (Verbose)."""
    provider = _StubProvider("   ")
    out = asyncio.run(
        summarize_chunk(
            segments=[{"type": "assistant", "phase": "intermediate", "text": "x"}],
            provider=provider,
        )
    )
    assert out is None


# --------------------------------------------------------------------------
# Char-triggered buffer
# --------------------------------------------------------------------------

def _inter(text: str) -> dict:
    return {"type": "assistant", "phase": "intermediate", "text": text}


def test_buffer_flushes_at_char_threshold() -> None:
    buf = SummarizationBuffer(char_threshold=100)
    # First block (60 chars) buffers, no flush.
    assert buf.observe(block_id=1, kind="assistant_intermediate", segment=_inter("a" * 60)) is None
    # Second block (60 chars) would push sum to 120 > 100 → flush the first run.
    pending = buf.observe(block_id=2, kind="assistant_intermediate", segment=_inter("b" * 60))
    assert pending is not None
    assert pending.block_ids == (1,)
    # The flushing block (2) starts the new chunk.
    pending2 = buf.flush_pending()
    assert pending2 is not None
    assert pending2.block_ids == (2,)


def test_buffer_below_threshold_does_not_flush() -> None:
    buf = SummarizationBuffer(char_threshold=DEFAULT_CHAR_THRESHOLD)
    for i in range(3):
        assert buf.observe(
            block_id=i, kind="assistant_intermediate", segment=_inter("short")
        ) is None
    pending = buf.flush_pending()
    assert pending is not None
    assert pending.block_ids == (0, 1, 2)


def test_buffer_ignores_final_and_user() -> None:
    buf = SummarizationBuffer(char_threshold=10)
    assert buf.observe(
        block_id=1, kind="assistant_final", segment=_inter("x" * 50)
    ) is None
    assert buf.observe(block_id=2, kind="user", segment={"type": "user", "text": "x" * 50}) is None
    assert buf.flush_pending() is None  # nothing summarizable buffered


def test_buffer_deterministic_under_prefix_growth() -> None:
    """Re-observing already-seen block ids (prefix re-projection) is a no-op.

    Two runs that observe the same sealed-block sequence — even if intermediate
    re-projections replay earlier ids — produce identical chunk boundaries.
    """
    def run(replay: bool) -> list[tuple[int, ...]]:
        buf = SummarizationBuffer(char_threshold=100)
        flushes: list[tuple[int, ...]] = []
        seq = [(1, 60), (2, 60), (3, 60)]
        for bid, n in seq:
            if replay:
                # Replay every prior id before the new one (prefix-grow noise).
                for pbid, pn in seq:
                    if pbid <= bid:
                        p = buf.observe(
                            block_id=pbid,
                            kind="assistant_intermediate",
                            segment=_inter("z" * pn),
                        )
                        if p is not None:
                            flushes.append(p.block_ids)
            else:
                p = buf.observe(
                    block_id=bid, kind="assistant_intermediate", segment=_inter("z" * n)
                )
                if p is not None:
                    flushes.append(p.block_ids)
        tail = buf.flush_pending()
        if tail is not None:
            flushes.append(tail.block_ids)
        return flushes

    assert run(replay=False) == run(replay=True)


# --------------------------------------------------------------------------
# Storage round-trip with attribution
# --------------------------------------------------------------------------

@pytest.fixture()
def conn():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute(
        "INSERT INTO conversations (conversation_id, agent_id, status, created_at, updated_at)"
        " VALUES ('conv-1', 'conv-1', 'in_progress', 't', 't')"
    )
    yield c
    c.close()


def test_condensed_column_dropped(conn) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    assert "condensed" not in cols


def test_write_and_read_chunk_summaries_with_attribution(conn) -> None:
    sid1 = conv_store.write_chunk_summary(
        conn, "conv-1", summary="Read the schema.", block_ids=[10, 11, 12]
    )
    sid2 = conv_store.write_chunk_summary(
        conn, "conv-1", summary="Edited the parser.", block_ids=[13, 14]
    )
    assert sid1 != sid2

    rows = conv_store.read_chunk_summaries(conn, "conv-1")
    assert [r.chunk_idx for r in rows] == [0, 1]
    assert rows[0].summary == "Read the schema."
    assert rows[0].block_ids == (10, 11, 12)
    assert rows[1].block_ids == (13, 14)


def test_write_chunk_summary_rejects_empty(conn) -> None:
    with pytest.raises(ValueError):
        conv_store.write_chunk_summary(conn, "conv-1", summary="   ", block_ids=[1])


def test_chunk_summaries_in_read_model_snapshot(repo_root_tmp_conn=None) -> None:
    """The conversations snapshot carries ordered chunk summaries + block ids."""
    import sqlite3

    from murder.app.protocol.read_models import ConversationChunkSummary

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute(
        "INSERT INTO conversations (conversation_id, agent_id, status, created_at, updated_at)"
        " VALUES ('conv-1', 'conv-1', 'in_progress', 't', 't')"
    )
    conv_store.write_chunk_summary(c, "conv-1", summary="First.", block_ids=[1, 2])
    conv_store.write_chunk_summary(c, "conv-1", summary="Second.", block_ids=[3])

    rows = conv_store.read_chunk_summaries(c, "conv-1")
    dtos = tuple(
        ConversationChunkSummary(
            summary_id=r.summary_id,
            chunk_idx=r.chunk_idx,
            summary=r.summary,
            block_ids=r.block_ids,
        )
        for r in rows
    )
    assert dtos[0].summary == "First."
    assert dtos[0].block_ids == (1, 2)
    assert dtos[1].block_ids == (3,)
    c.close()
