"""ConversationsStore — adapts ConversationProjection to the Store contract.

No Textual imports; pure data. Implements the React useSyncExternalStore shape
so this class carries over verbatim to a future web UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.service.client_api import ConversationsSnapshot
from murder.app.tui.conversations import (
    ConversationProjection,
    RenderConversation,
)
from murder.app.tui.stores.base import BaseStore


@dataclass(frozen=True)
class ConversationsStoreSnapshot:
    """Immutable whole-store snapshot; sorted by conversation_id for stable equality."""

    conversations: tuple[RenderConversation, ...]


class ConversationsStore(BaseStore[ConversationsStoreSnapshot]):
    """Stream-fed store for all in-session conversations.

    bootstrap() + apply_event() funnel all mutations through _set() so
    subscribers are notified only on real state changes.
    """

    def __init__(self) -> None:
        super().__init__(ConversationsStoreSnapshot(conversations=()))
        self._projection = ConversationProjection()
        self._known_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def bootstrap(self, snapshot: ConversationsSnapshot) -> None:
        self._projection.bootstrap(snapshot)
        self._known_ids = {conv.conversation_id for conv in snapshot.conversations}
        self._set(self._build_snapshot())

    def apply_event(self, event: object) -> str | None:
        conversation_id = self._projection.apply_event(event)
        if conversation_id is not None:
            self._known_ids.add(conversation_id)
            self._set(self._build_snapshot())
        return conversation_id

    # ------------------------------------------------------------------
    # Query helpers (callers need these during the bridge stage)
    # ------------------------------------------------------------------

    def conversation_for(self, conversation_id: str) -> RenderConversation | None:
        return self._projection.conversation_for(conversation_id)

    def doc_for(self, conversation_id: str) -> dict[str, object] | None:
        return self._projection.doc_for(conversation_id)

    def conversation_id_for_agent(self, agent_id: str) -> str | None:
        return self._projection.conversation_id_for_agent(agent_id)

    def conversation_id_for_agent_prefix(self, prefix: str) -> str | None:
        return self._projection.conversation_id_for_agent_prefix(prefix)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_snapshot(self) -> ConversationsStoreSnapshot:
        conversations = tuple(
            conv
            for cid in sorted(self._known_ids)
            if (conv := self._projection.conversation_for(cid)) is not None
        )
        return ConversationsStoreSnapshot(conversations=conversations)
