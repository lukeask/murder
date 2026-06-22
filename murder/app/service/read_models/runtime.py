"""Runtime-panel snapshot builders: crows, conversations, schedule."""

from __future__ import annotations

import json
from collections import defaultdict
from contextlib import closing
from datetime import datetime

from murder.app.service.client_api import (
    ConversationBlockSummary,
    ConversationChunkSummary,
    ConversationsSnapshot,
    ConversationSummary,
    CrowSessionSummary,
    CrowSnapshot,
    InvalidationKeys,
    ScheduleSnapshot,
)
from murder.app.service.schedule_snapshot import build_schedule_snapshot

from ._common import (
    ReadModelBase,
    _keep_failed_session,
    _optional_str,
    _parse_datetime,
)


class RuntimeReadModel(ReadModelBase):
    """Build crow/conversation/schedule snapshots."""

    def get_schedule_snapshot(self) -> ScheduleSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            return build_schedule_snapshot(
                conn,
                as_of=as_of,
                invalidation_key=self.keys.current_key(InvalidationKeys.schedule),
            )

    def get_crow_snapshot(self) -> CrowSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT a.agent_id, a.role, a.ticket_id, a.status, a.session,
                       COALESCE(a.harness, t.harness) AS harness,
                       COALESCE(a.model, t.model) AS model,
                       a.worktree_path,
                       a.started_at, a.last_heartbeat_at,
                       COALESCE(t.title, '') AS title,
                       COALESCE(t.status, '') AS ticket_status
                  FROM agents a
                  LEFT JOIN tickets t ON t.id = a.ticket_id
                 WHERE a.status NOT IN ('done', 'dead')
                 ORDER BY
                       CASE a.status
                         WHEN 'escalating' THEN 0
                         WHEN 'blocked' THEN 1
                         WHEN 'running' THEN 2
                         WHEN 'idle' THEN 3
                         WHEN 'failed' THEN 4
                         ELSE 5
                       END,
                       a.started_at DESC,
                       a.agent_id
                """
            ).fetchall()
            ticket_ids = [str(r["ticket_id"]) for r in rows if r["ticket_id"]]
            open_by_ticket: dict[str, tuple[int, int]] = {}
            if ticket_ids:
                placeholders = ",".join("?" * len(ticket_ids))
                for esc in conn.execute(
                    f"""
                    SELECT ticket_id, COUNT(*) AS n, MAX(severity) AS max_sev
                      FROM escalations
                     WHERE resolved = 0 AND ticket_id IN ({placeholders})
                     GROUP BY ticket_id
                    """,
                    ticket_ids,
                ).fetchall():
                    open_by_ticket[str(esc["ticket_id"])] = (
                        int(esc["n"]),
                        int(esc["max_sev"] or 0),
                    )
        sessions = tuple(
            CrowSessionSummary(
                agent_id=str(row["agent_id"]),
                role=str(row["role"]),
                ticket_id=_optional_str(row["ticket_id"]) or None,
                ticket_title=str(row["title"] or ""),
                status=str(row["status"]),
                session_name=_optional_str(row["session"]),
                harness=_optional_str(row["harness"]),
                last_seen=_parse_datetime(row["last_heartbeat_at"]),
                started_at=_parse_datetime(row["started_at"]),
                ticket_status=_optional_str(row["ticket_status"]) or None,
                worktree_path=_optional_str(row["worktree_path"]),
                model=_optional_str(row["model"]),
                open_escalations=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[0],
                max_severity=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[1],
            )
            for row in rows
        )
        # done/dead are excluded in SQL; drop stale failed agents here so the
        # wire roster never carries them (Ink does no client-side filtering).
        sessions = tuple(s for s in sessions if _keep_failed_session(s, now=as_of))
        return CrowSnapshot(
            sessions=sessions,
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.crows),
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
