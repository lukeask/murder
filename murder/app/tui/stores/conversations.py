"""ConversationsStore — adapts ConversationProjection to the Store contract.

No Textual imports; pure data. Implements the React useSyncExternalStore shape
so this class carries over verbatim to a future web UI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from murder.app.service.client_api import ConversationsSnapshot
from murder.app.tui.conversations import (
    ConversationProjection,
    RenderConversation,
)
from murder.app.tui.stores.base import BaseStore


def _segment_to_turn(segment: Mapping[str, Any]) -> tuple[str, str] | None:
    """Convert a single conversation segment to a (who, text) turn pair.

    Headless reimplementation of crows_view._segment_text — no Textual
    imports, no logger (unknown types are silently skipped rather than warned).
    Only the subset of types that appear in collaborative-planner conversations
    needs to be rendered for ChatLog.
    """
    seg_type = segment.get("type")
    if seg_type == "user":
        text = segment.get("text")
        return ("user", text) if isinstance(text, str) and text.strip() else None
    if seg_type == "assistant":
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return ("assistant", text)
    if seg_type == "tool_call":
        title = str(segment.get("title") or "").strip()
        if not title:
            return None
        parts = [title]
        tool_input = segment.get("input")
        if isinstance(tool_input, str) and tool_input.strip():
            parts.append(f"$ {tool_input}")
        result = segment.get("result")
        if isinstance(result, str) and result.strip():
            parts.append(result)
        if segment.get("elided"):
            parts.append("[collapsed]")
        return ("tool", "\n".join(parts))
    if seg_type == "plan_update":
        title = str(segment.get("title") or "").strip()
        items = segment.get("items")
        if not title or not isinstance(items, list):
            return None
        lines = [title]
        for item in items:
            if not isinstance(item, Mapping):
                continue
            marker = "x" if item.get("done") else " "
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"[{marker}] {text}")
        return ("plan", "\n".join(lines))
    if seg_type == "agent_event":
        name = str(segment.get("name") or "").strip()
        status = str(segment.get("status") or "").strip()
        elapsed = str(segment.get("elapsed") or "").strip()
        parts = [part for part in (status, name, elapsed) if part]
        return ("agent", " · ".join(parts)) if parts else None
    if seg_type == "choice_prompt":
        question = str(segment.get("question") or "").strip()
        options = segment.get("options")
        if not question:
            return None
        lines = [question]
        if segment.get("answered"):
            chosen = segment.get("chosen")
            if isinstance(options, list):
                for option in options:
                    if not isinstance(option, Mapping):
                        continue
                    if option.get("number") != chosen:
                        continue
                    label = str(option.get("label") or "").strip()
                    if label:
                        lines.append(f"selected: {chosen}. {label}")
                    break
            return ("prompt", "\n".join(lines))
        if isinstance(options, list):
            for option in options:
                if not isinstance(option, Mapping):
                    continue
                number = option.get("number")
                label = str(option.get("label") or "").strip()
                if label:
                    lines.append(f"{number}. {label}")
        return ("prompt", "\n".join(lines))
    if seg_type == "notice":
        message = str(segment.get("message") or segment.get("text") or "").strip()
        severity = str(segment.get("severity") or "").strip()
        if not message:
            return None
        return ("notice", f"{severity}: {message}" if severity else message)
    return None


def doc_to_chat_turns(doc: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Convert a conversation doc (from RenderConversation.to_doc()) to turn tuples.

    Returns an immutable tuple of (who, text) pairs for stable snapshot equality.
    Headless; no Textual dependency.
    """
    turns: list[tuple[str, str]] = []
    segments = doc.get("segments")
    if not isinstance(segments, list):
        return ()
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        rendered = _segment_to_turn(segment)
        if rendered is not None:
            turns.append(rendered)
    return tuple(turns)


@dataclass(frozen=True)
class ConversationsStoreSnapshot:
    """Immutable whole-store snapshot; sorted by conversation_id for stable equality."""

    conversations: tuple[RenderConversation, ...]
    # Pre-derived chat turns keyed by conversation_id for direct widget rendering.
    turns_by_id: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()


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
        turns_by_id = tuple(
            (conv.conversation_id, doc_to_chat_turns(conv.to_doc()))
            for conv in conversations
        )
        return ConversationsStoreSnapshot(
            conversations=conversations,
            turns_by_id=turns_by_id,
        )
