"""Char-triggered rolling summarization buffer (TUIchat Phase 4).

Sits in the producer path next to :class:`ConversationProducer`. As sealed
*intermediate* conversation blocks arrive it accumulates their char lengths;
when adding the next block would push the running char-sum past a threshold
(~3000 chars) it flushes the buffered run as **one** summary call and rolls the
buffer.

Hot-path contract (design spike, see TUIchat-4 done-note)
---------------------------------------------------------
The char-accounting and roll decision (`observe`) are O(1)-amortized, purely
synchronous, and run inline on the projection tick — they add no measurable
latency to ``feed()``/publish. The *summary call itself* is dispatched via
``asyncio.create_task`` (fire-and-forget) by the caller-supplied ``schedule``
hook: ``observe`` returns a ready-to-summarize :class:`PendingChunk` and never
awaits the network. The producer therefore never sits between a pane capture
and the bus publish waiting on the summarizer — streaming stays on its own path.

Determinism
-----------
A block is only ever buffered once (tracked by block id). The same prefix-grown
frame re-projected does not re-buffer already-seen blocks, so chunk boundaries
are deterministic for a given sequence of sealed blocks regardless of how many
intermediate (unsealed) frames preceded them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Roll when adding the next block would push the running char-sum past this.
DEFAULT_CHAR_THRESHOLD = 3000

# Block kinds that count as summarizable *intermediate* activity. The final
# assistant reply (`assistant_final`) is deliberately excluded — it is rendered
# verbatim and must never be summarized. `user` blocks are authoritative turns,
# also excluded.
_SUMMARIZABLE_KINDS = frozenset(
    {
        "assistant_intermediate",
        "tool_call",
        "plan_update",
        "agent_event",
        "choice_prompt",
    }
)


def _segment_char_len(segment: Mapping[str, Any]) -> int:
    """Rough char weight of a block's payload for buffer accounting.

    Tool calls weigh only their descriptor-relevant fields (title) — not raw
    input/result payloads — mirroring the summarizer's descriptor reduction so
    the threshold reflects what actually gets summarized.
    """
    seg_type = str(segment.get("type", ""))
    if seg_type == "tool_call":
        return len(str(segment.get("title") or ""))
    if seg_type == "assistant":
        return len(str(segment.get("text") or ""))
    if seg_type == "plan_update":
        items = segment.get("items") or []
        return len(str(segment.get("title") or "")) + sum(
            len(str(i.get("text", ""))) for i in items if isinstance(i, Mapping)
        )
    # Fallback: total length of stringified scalar fields.
    return sum(len(str(v)) for v in segment.values() if isinstance(v, (str, int, float)))


@dataclass
class PendingChunk:
    """A buffered run ready to be summarized off the hot path."""

    block_ids: tuple[int, ...]
    segments: tuple[Mapping[str, Any], ...]
    state: str


@dataclass
class SummarizationBuffer:
    """Accumulate sealed intermediate blocks; emit a chunk at the char threshold.

    Stateful and synchronous. The owner calls :meth:`observe` once per sealed
    block (in block order); when a chunk is ready it is returned for the owner
    to schedule off the hot path.
    """

    char_threshold: int = DEFAULT_CHAR_THRESHOLD
    _ids: list[int] = field(default_factory=list)
    _segments: list[Mapping[str, Any]] = field(default_factory=list)
    _char_sum: int = 0
    _seen: set[int] = field(default_factory=set)
    last_state: str = "working"

    def observe(
        self,
        *,
        block_id: int,
        kind: str,
        segment: Mapping[str, Any],
        state: str = "working",
    ) -> PendingChunk | None:
        """Account one sealed block; return a chunk to flush if the threshold trips.

        - Final/user/non-summarizable blocks are ignored (never buffered).
        - A block already observed (same id) is ignored — keeps chunking
          deterministic under prefix-growing re-projection.
        - If adding this block would push the running sum past the threshold AND
          the buffer is non-empty, the buffered run flushes *first* (returned),
          the buffer rolls, then this block starts the new chunk.
        """
        self.last_state = state
        if kind not in _SUMMARIZABLE_KINDS:
            return None
        if block_id in self._seen:
            return None

        weight = _segment_char_len(segment)
        flushed: PendingChunk | None = None
        if self._ids and self._char_sum + weight > self.char_threshold:
            flushed = self._roll(state)

        self._seen.add(block_id)
        self._ids.append(block_id)
        self._segments.append(segment)
        self._char_sum += weight
        return flushed

    def flush_pending(self, state: str | None = None) -> PendingChunk | None:
        """Force-flush any buffered run (e.g. on conversation completion)."""
        if not self._ids:
            return None
        return self._roll(state if state is not None else self.last_state)

    def _roll(self, state: str) -> PendingChunk:
        chunk = PendingChunk(
            block_ids=tuple(self._ids),
            segments=tuple(self._segments),
            state=state,
        )
        self._ids = []
        self._segments = []
        self._char_sum = 0
        return chunk
