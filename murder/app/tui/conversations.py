"""In-memory conversation projection for thin TUI transcript rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from murder.app.service.client_api import (
    ConversationBlockSummary,
    ConversationSummary,
    ConversationsSnapshot,
)


@dataclass(frozen=True)
class RenderConversation:
    conversation_id: str
    harness: str | None
    model: str | None
    state: str | None
    condensed: str | None
    segments: tuple[Mapping[str, object], ...]

    def to_doc(self) -> dict[str, object]:
        return {
            "harness": self.harness or "",
            "state": self.state or "awaiting_input",
            "condensed": self.condensed,
            "segments": [dict(segment) for segment in self.segments],
        }


@dataclass(frozen=True)
class _StoredBlock:
    id: int | None
    ordinal: int
    kind: str
    payload: Mapping[str, object]
    sealed: bool
    service_received_at: str


class ConversationProjection:
    """Mutable event-sourced projection held only for the current TUI session."""

    def __init__(self) -> None:
        self._meta: dict[str, ConversationSummary] = {}
        self._blocks: dict[str, list[_StoredBlock]] = {}
        self._agent_to_conversation: dict[str, str] = {}

    def bootstrap(self, snapshot: ConversationsSnapshot) -> None:
        self._meta = {conv.conversation_id: conv for conv in snapshot.conversations}
        self._agent_to_conversation = {
            conv.agent_id: conv.conversation_id
            for conv in snapshot.conversations
            if conv.agent_id
        }
        self._blocks = {
            conv.conversation_id: [_block_from_summary(block) for block in conv.blocks]
            for conv in snapshot.conversations
        }

    def apply_event(self, event: object) -> str | None:
        conversation_id = str(getattr(event, "conversation_id", "") or "")
        if not conversation_id:
            return None
        raw_block = getattr(event, "block", None)
        if not isinstance(raw_block, Mapping):
            return None
        block = _block_from_wire(raw_block)
        agent_id = str(getattr(event, "agent_id", "") or "")
        if agent_id:
            self._agent_to_conversation[agent_id] = conversation_id
        blocks = self._blocks.setdefault(conversation_id, [])
        idx = _matching_block_index(blocks, block)
        if idx is None:
            blocks.append(block)
            blocks.sort(key=lambda item: item.ordinal)
        else:
            blocks[idx] = block
        return conversation_id

    def conversation_id_for_agent(self, agent_id: str) -> str | None:
        return self._agent_to_conversation.get(agent_id)

    def conversation_id_for_agent_prefix(self, prefix: str) -> str | None:
        for agent_id, conversation_id in sorted(self._agent_to_conversation.items()):
            if agent_id == prefix or agent_id.startswith(f"{prefix}-"):
                return conversation_id
        return None

    def doc_for(self, conversation_id: str) -> dict[str, object] | None:
        conversation = self.conversation_for(conversation_id)
        return conversation.to_doc() if conversation is not None else None

    def conversation_for(self, conversation_id: str) -> RenderConversation | None:
        blocks = self._blocks.get(conversation_id)
        meta = self._meta.get(conversation_id)
        if not blocks and meta is None:
            return None
        segments = tuple(dict(block.payload) for block in sorted(blocks or (), key=lambda b: b.ordinal))
        return RenderConversation(
            conversation_id=conversation_id,
            harness=meta.harness if meta is not None else None,
            model=meta.model if meta is not None else None,
            state=meta.live_state if meta is not None else None,
            condensed=meta.condensed if meta is not None else None,
            segments=segments,
        )


def _matching_block_index(blocks: list[_StoredBlock], block: _StoredBlock) -> int | None:
    if block.id is not None:
        for idx, candidate in enumerate(blocks):
            if candidate.id == block.id:
                return idx
    for idx, candidate in enumerate(blocks):
        if candidate.ordinal == block.ordinal:
            return idx
    return None


def _block_from_summary(block: ConversationBlockSummary) -> _StoredBlock:
    return _StoredBlock(
        id=block.id,
        ordinal=block.ordinal,
        kind=block.kind,
        payload=dict(block.payload),
        sealed=block.sealed,
        service_received_at=block.service_received_at,
    )


def _block_from_wire(raw: Mapping[str, Any]) -> _StoredBlock:
    raw_id = raw.get("id")
    return _StoredBlock(
        id=raw_id if isinstance(raw_id, int) else None,
        ordinal=int(raw.get("ordinal") or 0),
        kind=str(raw.get("kind") or ""),
        payload=dict(raw.get("payload") if isinstance(raw.get("payload"), Mapping) else {}),
        sealed=bool(raw.get("sealed")),
        service_received_at=str(raw.get("service_received_at") or ""),
    )
