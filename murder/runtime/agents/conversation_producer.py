"""ConversationProducer — portable per-conversation projection unit.

Owns one conversation's accumulator + hash-skip; drives projection and publish
without any tmux or process knowledge so it stays unit-testable in isolation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from murder.llm.harnesses.transcripts import TranscriptAccumulator
from murder.state.persistence import conversation as conv_store

if TYPE_CHECKING:
    import sqlite3


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
    ) -> None:
        self.conversation_id = conversation_id
        self._db = db
        self._publish = publish
        self._acc = TranscriptAccumulator(harness_kind, system_prompt=system_prompt)
        self._last_pane_hash: str | None = None

    async def poll(self, pane: str) -> None:
        """Feed a new pane capture; no-op if the pane hasn't changed since last poll."""
        h = hashlib.sha256(pane.encode("utf-8", errors="replace")).hexdigest()
        if h == self._last_pane_hash:
            return
        self._last_pane_hash = h

        # Refresh murder-owned user turns so markerless grammars can recognise
        # (and drop) user content echoed in the pane.
        self._acc.user_texts = conv_store.read_user_texts(self._db, self.conversation_id)
        self._acc.feed(pane)
        doc = self._acc.to_dict()

        _merged, changes = conv_store.project_parsed_doc_with_changes(
            self._db, self.conversation_id, doc
        )
        for change in changes:
            await self._publish(str(change.action), conv_store.block_to_wire(change.block))
