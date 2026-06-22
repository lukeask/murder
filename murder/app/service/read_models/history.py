"""History-panel snapshot builder: the user-intention feed."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta

from murder.app.service.client_api import (
    HistoryItemSummary,
    HistorySnapshot,
    InvalidationKeys,
)
from murder.state.persistence import history as history_store

from ._common import (
    RESUMABLE_HARNESS,
    STALE_AFTER_HOURS,
    ReadModelBase,
    _extract_user_text,
    _is_noise,
    _is_stale,
    _optional_str,
)


class HistoryReadModel(ReadModelBase):
    """Build the user-intention history feed."""

    def get_history_snapshot(self) -> HistorySnapshot:
        """Build the user-intention history feed.

        The spine is the durable ``conversation_blocks kind='user'`` record
        (written at the send boundary). Each row is joined against its
        conversation (for harness/session/status) and the ``history_status``
        overlay, then a zero-LLM status is derived per row. The view filters
        (loose-threads vs all) and orders client-side; this returns the full,
        noise-filtered feed in newest-first order with derived state.
        """
        as_of = datetime.utcnow()
        stale_before = as_of - timedelta(hours=STALE_AFTER_HOURS)
        with closing(self._connect()) as conn:
            overlay = history_store.get_status_map(conn)
            rows = conn.execute(
                """
                SELECT b.conversation_id, b.ordinal, b.payload_json,
                       b.service_received_at,
                       c.agent_id, c.harness, c.harness_session_id,
                       c.status AS conversation_status
                  FROM conversation_blocks b
                  JOIN conversations c
                    ON c.conversation_id = b.conversation_id
                 WHERE b.kind = 'user'
                 ORDER BY b.service_received_at DESC, b.conversation_id, b.ordinal DESC
                """
            ).fetchall()
        items: list[HistoryItemSummary] = []
        for row in rows:
            text = _extract_user_text(row["payload_json"])
            if _is_noise(text):
                continue
            item_id = f"{row['conversation_id']}:{int(row['ordinal'])}"
            ts = str(row["service_received_at"])
            overlay_row = overlay.get(item_id)
            if overlay_row is not None and overlay_row[0] == "dismissed":
                status = "dismissed"
            elif _is_stale(ts, stale_before):
                status = "stale"
            else:
                status = "open"
            harness = _optional_str(row["harness"])
            conversation_status = str(row["conversation_status"])
            resumable = (
                harness == RESUMABLE_HARNESS
                and conversation_status == "complete"
                and bool(row["harness_session_id"])
            )
            items.append(
                HistoryItemSummary(
                    item_id=item_id,
                    text=text,
                    target=str(row["agent_id"]),
                    ts=ts,
                    status=status,
                    harness=harness,
                    conversation_status=conversation_status,
                    resumable=resumable,
                )
            )
        return HistorySnapshot(
            items=tuple(items),
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.history),
        )
