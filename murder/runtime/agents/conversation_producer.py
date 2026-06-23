"""ConversationProducer — portable per-conversation projection unit.

Owns one conversation's accumulator + hash-skip; drives projection and publish
without any tmux or process knowledge so it stays unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from murder.llm.harnesses.transcript_summarize import SummaryProvider, summarize_chunk
from murder.llm.harnesses.transcripts import TranscriptAccumulator
from murder.runtime.agents.summarization_buffer import PendingChunk, SummarizationBuffer
from murder.state.persistence import conversation as conv_store

if TYPE_CHECKING:
    import sqlite3

_log = logging.getLogger(__name__)


class ConversationProducer:
    """Accumulates and projects one conversation's pane transcript into the DB.

    Injected with a db connection and a publish callback so it carries no tmux
    or process knowledge and is fully unit-testable.  One instance per agent.

    publish(action, block_wire) is called for each ConversationBlockChange
    returned by project_parsed_doc_with_changes; the caller wraps it into
    the appropriate bus event.
    """

    def __init__(
        self,
        conversation_id: str,
        harness_kind: str,
        system_prompt: str | None,
        db: sqlite3.Connection,
        publish: Callable[[str, dict[str, Any]], Awaitable[None]],
        *,
        summary_provider: SummaryProvider | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self._db = db
        self._publish = publish
        self._acc = TranscriptAccumulator(harness_kind, system_prompt=system_prompt)
        self._last_pane_hash: str | None = None
        # The parsed harness UI state from the most recent non-skipped poll
        # (working / awaiting_input / awaiting_approval). Read by the agent's
        # projection tick for queued-message delivery + conversation.state push.
        self.last_state: str | None = None
        # Condensed-view rolling summarization (TUIchat Phase 4). The buffer's
        # char-accounting is synchronous and inline; the summary call itself is
        # dispatched off the hot path via asyncio.create_task so it never adds
        # latency to the pane→bus projection. Disabled (no summarization) when
        # no provider can be built — Condensed then falls back to Verbose.
        self._summary_buffer = SummarizationBuffer()
        self._summary_provider = summary_provider
        self._summary_provider_resolved = summary_provider is not None
        self._summary_tasks: set[asyncio.Task[None]] = set()
        # Tracks the prior poll's parsed state so we can detect the working→idle
        # turn boundary and force-flush the summarization buffer's tail (a short
        # turn never crosses the rolling char threshold, so without this its
        # intermediate work would never be summarized → Condensed == Verbose).
        self._prev_summary_state: str = "working"

    async def poll(self, pane: str) -> bool:
        """Feed a new pane capture; no-op if the pane hasn't changed since last poll.

        Returns ``True`` iff this poll produced real block changes (i.e. the pane
        hash advanced AND the reconcile yielded at least one ConversationBlockChange).
        The caller (``project_once``) uses this to gate the key-only ``plan`` re-sort
        invalidation (F11 H1): ``project_parsed_doc_with_changes`` rewrites
        ``agent_messages`` and so bumps the planner's MAX(captured_at) that
        ``get_plans_snapshot`` orders by — but only when content actually grew, not on
        every hash-skipped poll tick.
        """
        h = hashlib.sha256(pane.encode("utf-8", errors="replace")).hexdigest()
        if h == self._last_pane_hash:
            return False
        self._last_pane_hash = h

        # Refresh murder-owned user turns so markerless grammars can recognise
        # (and drop) user content echoed in the pane.
        self._acc.user_texts = conv_store.read_user_texts(self._db, self.conversation_id)
        self._acc.feed(pane)
        doc = self._acc.to_dict()
        self.last_state = doc.get("state")

        _merged, changes = conv_store.project_parsed_doc_with_changes(
            self._db, self.conversation_id, doc
        )
        for change in changes:
            await self._publish(str(change.action), conv_store.block_to_wire(change.block))

        # Off-hot-path condensed summarization: account sealed intermediate
        # blocks and dispatch any ready chunk via create_task (no await here).
        self._observe_for_summary(changes, str(self.last_state or "working"))
        return bool(changes)

    def _observe_for_summary(
        self,
        changes: Sequence[conv_store.ConversationBlockChange],
        state: str,
    ) -> None:
        """Feed *sealed* blocks into the summarization buffer; schedule flushes.

        Only sealed blocks are buffered: an unsealed (still-growing) trailing
        block is not final content yet, and buffering it would re-buffer on each
        prefix-grown frame. The buffer ignores final/non-summarizable kinds and
        de-dups by block id, so this is safe to call every poll.
        """
        for change in changes:
            block = change.block
            if not block.sealed or block.id is None:
                continue
            pending = self._summary_buffer.observe(
                block_id=int(block.id),
                kind=block.kind,
                segment=dict(block.payload),
                state=state,
            )
            if pending is not None:
                self._schedule_summary(pending)

        # Turn-boundary flush: when the harness goes working→idle the turn's
        # intermediate work is complete and its final reply is being rendered
        # verbatim. A short turn never crosses the rolling char threshold, so its
        # buffered run would otherwise sit unflushed forever (Condensed would
        # render identically to Verbose). Flush the tail here so every completed
        # turn yields at least one chunk summary.
        if self._prev_summary_state == "working" and state != "working":
            tail = self._summary_buffer.flush_pending(state)
            if tail is not None:
                self._schedule_summary(tail)
        self._prev_summary_state = state

    def _schedule_summary(self, chunk: PendingChunk) -> None:
        """Dispatch one chunk summary as a fire-and-forget background task.

        Runs strictly off the streaming hot path: ``poll`` returns immediately;
        the network round-trip and DB write happen in this task. Single-threaded
        asyncio means the synchronous DB write cannot interleave another poll's
        write mid-transaction.
        """
        task = asyncio.create_task(
            self._summarize_and_store(chunk),
            name=f"condense-{self.conversation_id}-{chunk.block_ids[:1]}",
        )
        self._summary_tasks.add(task)
        task.add_done_callback(self._summary_tasks.discard)

    async def _summarize_and_store(self, chunk: PendingChunk) -> None:
        provider = self._resolve_provider()
        if provider is None:
            return
        try:
            summary = await summarize_chunk(
                segments=list(chunk.segments),
                state=chunk.state,
                provider=provider,
            )
        except Exception:  # noqa: BLE001 — summarization is best-effort
            _log.warning("condense: chunk summary failed", exc_info=True)
            return
        # Empty-summary guard: a blank result degrades to Verbose (no write).
        if not summary:
            return
        try:
            conv_store.write_chunk_summary(
                self._db,
                self.conversation_id,
                summary=summary,
                block_ids=chunk.block_ids,
            )
            self._db.commit()
        except Exception:  # noqa: BLE001
            _log.warning("condense: chunk summary persist failed", exc_info=True)
            return
        await self._publish(
            "chunk-summarized",
            {
                "conversation_id": self.conversation_id,
                "summary": summary,
                "block_ids": list(chunk.block_ids),
            },
        )

    def _resolve_provider(self) -> SummaryProvider | None:
        """Lazily build the default free-tier provider once (cached)."""
        if not self._summary_provider_resolved:
            from murder.llm.harnesses.transcript_summarize import (  # noqa: PLC0415
                build_default_summary_provider,
            )

            self._summary_provider = build_default_summary_provider()
            self._summary_provider_resolved = True
        return self._summary_provider
