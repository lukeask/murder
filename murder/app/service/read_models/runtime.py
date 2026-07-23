"""Runtime-panel snapshot builders: conversations and schedule."""

from __future__ import annotations

import json
from collections import defaultdict
from contextlib import closing
from datetime import datetime

from murder.app.protocol.read_models import (
    ConversationBlockSummary,
    ConversationChunkSummary,
    ConversationsSnapshot,
    ConversationSummary,
    InvalidationKeys,
    ScheduleSnapshot,
)
from murder.app.service.schedule_snapshot import build_schedule_snapshot

from ._common import (
    ReadModelBase,
    _optional_str,
)


class RuntimeReadModel(ReadModelBase):
    """Build conversation and schedule snapshots."""

    def get_schedule_snapshot(self) -> ScheduleSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            return build_schedule_snapshot(
                conn,
                as_of=as_of,
                invalidation_key=self.keys.current_key(InvalidationKeys.schedule),
            )

    def get_conversations_snapshot(self) -> ConversationsSnapshot:
        """Return active conversation histories for a newly connected TUI."""
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            conv_rows = conn.execute(
                """
                SELECT conversation_id, agent_id, harness, model, harness_session_id,
                       live_state, queued_message, status
                  FROM conversations
                 WHERE status = 'in_progress'
                 ORDER BY updated_at DESC, conversation_id
                """
            ).fetchall()
            block_rows = conn.execute(
                """
                SELECT conversation_id, id, ordinal, kind, payload_json, sealed,
                       service_received_at
                  FROM conversation_blocks
                 WHERE conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY conversation_id, ordinal
                """
            ).fetchall()
            summary_rows = conn.execute(
                """
                SELECT conversation_id, summary_id, chunk_idx, summary
                  FROM conversation_chunk_summaries
                 WHERE conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY conversation_id, chunk_idx
                """
            ).fetchall()
            summary_block_rows = conn.execute(
                """
                SELECT csb.summary_id AS summary_id, csb.block_id AS block_id
                  FROM chunk_summary_blocks csb
                  JOIN conversation_chunk_summaries ccs
                    ON ccs.summary_id = csb.summary_id
                 WHERE ccs.conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY csb.summary_id, csb.block_id
                """
            ).fetchall()
        block_ids_by_summary: dict[int, list[int]] = defaultdict(list)
        for row in summary_block_rows:
            block_ids_by_summary[int(row["summary_id"])].append(int(row["block_id"]))
        summaries_by_conversation: dict[str, list[ConversationChunkSummary]] = defaultdict(list)
        for row in summary_rows:
            summaries_by_conversation[str(row["conversation_id"])].append(
                ConversationChunkSummary(
                    summary_id=int(row["summary_id"]),
                    chunk_idx=int(row["chunk_idx"]),
                    summary=str(row["summary"]),
                    block_ids=tuple(block_ids_by_summary.get(int(row["summary_id"]), ())),
                )
            )
        blocks_by_conversation: dict[str, list[ConversationBlockSummary]] = defaultdict(list)
        for row in block_rows:
            blocks_by_conversation[str(row["conversation_id"])].append(
                ConversationBlockSummary(
                    id=int(row["id"]),
                    conversation_id=str(row["conversation_id"]),
                    ordinal=int(row["ordinal"]),
                    kind=str(row["kind"]),
                    payload=json.loads(str(row["payload_json"] or "{}")),
                    sealed=bool(row["sealed"]),
                    service_received_at=str(row["service_received_at"]),
                )
            )
        conversations = tuple(
            ConversationSummary(
                conversation_id=str(row["conversation_id"]),
                agent_id=str(row["agent_id"]),
                harness=_optional_str(row["harness"]),
                model=_optional_str(row["model"]),
                harness_session_id=_optional_str(row["harness_session_id"]),
                live_state=_optional_str(row["live_state"]),
                chunk_summaries=tuple(summaries_by_conversation[str(row["conversation_id"])]),
                queued_message=_optional_str(row["queued_message"]),
                status=str(row["status"]),
                blocks=tuple(blocks_by_conversation[str(row["conversation_id"])]),
            )
            for row in conv_rows
        )
        return ConversationsSnapshot(
            conversations=conversations,
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.conversations),
        )
