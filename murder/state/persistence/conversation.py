"""Persisted user↔agent conversation logs.

Two storage paths live side-by-side in this module:

**Legacy flat-turns path** (``merge_transcript``, ``read_conversation``, ``clear``):
  An interactive harness renders its chat in a tmux pane. ``parse_transcript``
  turns a pane capture into an ordered list of ``(role, text)`` turns.
  ``merge_transcript`` reconciles a fresh parse against what's already persisted
  so the stored log is the longest, most complete transcript observed.
  Still used for compatibility. See ``agent_messages`` table.

**JSON conversation-block path** (Phase 1.b):
  Parsed segment dicts are stored block-per-row in ``conversation_blocks``
  with full payload JSON, service-received timestamps, and a "live block" rule:
  at most one trailing ``sealed=0`` row per conversation at any time.
  A ``conversations`` row holds metadata per conversation session.
  See :func:`merge_conversation_doc`, :func:`read_conversation_blocks`, etc.

Both paths are independent. The JSON path is the one wired into the live app
(see ``project_parsed_doc_with_changes`` / ``append_user_message`` callers in
``runtime/agents/base.py`` and ``conversation_producer.py``).
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input
from murder.state.persistence.agents import (
    append_agent_message,
    get_agent_messages,
    replace_agent_messages,
)
from murder.state.persistence.connection import RepoDb

# ---------------------------------------------------------------------------
# Legacy flat-turns path (agent_messages table) — unchanged
# ---------------------------------------------------------------------------

# (role, text) — deliberately a plain tuple for UI transcript rendering.
Turn = tuple[str, str]


def _invalidate_conversations(db: RepoDb, subject_key: str) -> None:
    conn = db.conn
    """Append a durable key-only invalidation for the conversations snapshot."""
    row = conn.execute(
        "SELECT COALESCE(MAX(generation), -1) + 1 AS generation "
        "FROM projection_inputs WHERE repository_id = ? AND projection = 'conversations' "
        "AND subject_key = ?",
        (db.repository_id, subject_key),
    ).fetchone()
    generation = int(row["generation"])
    append_projection_input(
        db,
        ProjectionInputDraft(
            input_id=uuid5(
                NAMESPACE_URL,
                f"conversations:{subject_key}:{generation}",
            ),
            projection="conversations",
            subject_key=subject_key,
            generation=generation,
        ),
    )


def read_conversation(db: RepoDb, agent_id: str) -> list[Turn]:
    return [(r["role"], r["body"]) for r in get_agent_messages(db, agent_id)]


def clear(db: RepoDb, agent_id: str) -> None:
    conn = db.conn
    """Drop the persisted log for ``agent_id`` — call when a fresh agent
    session starts, so a new run does not show the previous run's chat.

    Clears *both* stores: the legacy flat ``agent_messages`` log and the 1.b
    JSON conversation store (blocks + metadata row). Leaving stale JSON blocks
    would make ``project_parsed_doc`` reconcile a new session's parse against a
    prior session's interleaved stream. ``conversation_id`` is the ``agent_id``
    (one live conversation per agent, 1.c).
    """
    existed = conn.execute(
        "SELECT 1 FROM conversations WHERE conversation_id = ? AND repository_id = ?",
        (agent_id, db.repository_id),
    ).fetchone()
    replace_agent_messages(db, agent_id, [])
    conn.execute(
        "DELETE FROM conversation_blocks WHERE conversation_id = ? AND repository_id = ?",
        (agent_id, db.repository_id),
    )
    conn.execute(
        "DELETE FROM conversations WHERE conversation_id = ? AND repository_id = ?",
        (agent_id, db.repository_id),
    )
    if existed is not None:
        _invalidate_conversations(db, agent_id)


def merge_transcript(
    db: RepoDb,
    agent_id: str,
    parsed: list[Turn],
    *,
    captured_at: str | None = None,
) -> list[Turn]:
    """Reconcile a fresh full-transcript ``parsed`` against the persisted log.

    Replace the stored log with ``parsed`` when it is at least as long and not
    byte-identical (covers an in-progress reply growing on the last turn). A
    shorter parse is treated as transient pane noise and ignored. Returns the
    effective transcript after merging.
    """
    stored = read_conversation(db, agent_id)
    if not parsed:
        return stored
    if len(parsed) > len(stored) or (len(parsed) == len(stored) and parsed != stored):
        replace_agent_messages(db, agent_id, parsed, captured_at=captured_at)
        return parsed
    return stored


# ---------------------------------------------------------------------------
# JSON conversation-block path (conversations + conversation_blocks tables)
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# Canonical set of block kinds.  assistant segment is split by phase so each
# kind maps unambiguously back to a segment dict stored in payload_json.
# `notice` carries service-originated usage/error notices written via
# `append_notice` (not emitted by any parser. The service injects them).
BLOCK_KINDS: tuple[str, ...] = (
    "user",
    "assistant_intermediate",
    "assistant_final",
    "tool_call",
    "plan_update",
    "agent_event",
    "choice_prompt",
    "notice",  # service-injected. See append_notice
)


def segment_to_block_kind(seg: dict[str, Any]) -> str:
    """Derive the block kind discriminant from a segment dict.

    For ``assistant`` segments the ``phase`` field determines whether the block
    is mutable (intermediate) or sealed-immediately (final).
    All other segment types map 1:1 to a block kind.
    """
    seg_type = seg["type"]
    if seg_type == "assistant":
        phase = seg.get("phase", "intermediate")
        return f"assistant_{phase}"
    return str(seg_type)


@dataclass
class ConversationBlock:
    """In-memory representation of a single conversation_blocks row."""

    id: int | None  # None before first DB write
    conversation_id: str
    ordinal: int
    kind: str
    payload: dict[str, Any]  # the original segment dict, lossless
    sealed: bool
    service_received_at: str


@dataclass
class ConversationBlockChange:
    """A store mutation that should be pushed to subscribed clients."""

    action: str  # "block-appended" | "block-updated"
    block: ConversationBlock
    # Projection consumers occasionally need to distinguish text growth from a
    # state-only update (notably live assistant markers).  Keep that history on
    # the change itself: the conversation block remains the sole identity and
    # no second cursor/fingerprint store is required.
    previous_payload: dict[str, Any] | None = None
    previous_sealed: bool | None = None


def block_to_wire(block: ConversationBlock) -> dict[str, Any]:
    """JSON-compatible representation used by notifications and read models."""
    return {
        "id": block.id,
        "conversation_id": block.conversation_id,
        "ordinal": block.ordinal,
        "kind": block.kind,
        "payload": block.payload,
        "sealed": block.sealed,
        "service_received_at": block.service_received_at,
    }


# ---------------------------------------------------------------------------
# Conversation-level CRUD
# ---------------------------------------------------------------------------


def upsert_conversation(
    db: RepoDb,
    *,
    conversation_id: str,
    agent_id: str,
    harness: str | None = None,
    model: str | None = None,
    harness_session_id: str | None = None,
    live_state: str | None = None,
    status: str = "in_progress",
) -> None:
    conn = db.conn
    """Insert or update the metadata row for a conversation session.

    Note: condensed summaries are no longer stored on this row — they live in
    conversation_chunk_summaries (see ``write_chunk_summary``).
    """
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM conversations WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO conversations
                (repository_id, conversation_id, agent_id, harness, model, harness_session_id,
                 live_state, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db.repository_id,
                conversation_id,
                agent_id,
                harness,
                model,
                harness_session_id,
                live_state,
                status,
                now,
                now,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE conversations
               SET harness            = COALESCE(?, harness),
                   model              = COALESCE(?, model),
                   harness_session_id = COALESCE(?, harness_session_id),
                   live_state         = COALESCE(?, live_state),
                   status             = ?,
                   updated_at         = ?
             WHERE conversation_id = ? AND repository_id = ?
            """,
            (
                harness,
                model,
                harness_session_id,
                live_state,
                status,
                now,
                conversation_id,
                db.repository_id,
            ),
        )


def set_conversation_status(
    db: RepoDb,
    conversation_id: str,
    status: str,
) -> None:
    conn = db.conn
    """Transition conversation status (in_progress → complete | stale)."""
    cur = conn.execute(
        "UPDATE conversations SET status = ?, updated_at = ? "
        "WHERE conversation_id = ? AND repository_id = ? AND status IS NOT ?",
        (status, _now(), conversation_id, db.repository_id, status),
    )
    if cur.rowcount:
        _invalidate_conversations(db, conversation_id)


def set_queued_message(
    db: RepoDb,
    conversation_id: str,
    message: str | None,
) -> None:
    conn = db.conn
    """Record (or clear, with ``None``) the busy-crow queued user message.

    DB-owns-runtime: the queued line the TUI renders survives a service or
    client restart because it lives here, not in agent memory alone.
    """
    cur = conn.execute(
        "UPDATE conversations SET queued_message = ?, updated_at = ? "
        "WHERE conversation_id = ? AND repository_id = ? AND queued_message IS NOT ?",
        (message, _now(), conversation_id, db.repository_id, message),
    )
    if cur.rowcount:
        _invalidate_conversations(db, conversation_id)


def get_queued_message(
    db: RepoDb,
    conversation_id: str,
) -> str | None:
    conn = db.conn
    """Read the queued-but-undelivered user message for a conversation."""
    row = conn.execute(
        "SELECT queued_message FROM conversations WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    if row is None:
        return None
    value = row["queued_message"]
    return str(value) if isinstance(value, str) and value else None


def set_harness_session_id(
    db: RepoDb,
    conversation_id: str,
    harness_session_id: str,
) -> None:
    conn = db.conn
    """Record the resume session id captured from the harness on graceful exit."""
    cur = conn.execute(
        """
        UPDATE conversations
           SET harness_session_id = ?, updated_at = ?
         WHERE conversation_id = ? AND repository_id = ? AND harness_session_id IS NOT ?
        """,
        (harness_session_id, _now(), conversation_id, db.repository_id, harness_session_id),
    )
    if cur.rowcount:
        _invalidate_conversations(db, conversation_id)


# ---------------------------------------------------------------------------
# Block-level persistence
# ---------------------------------------------------------------------------


def _seal_live_block(db: RepoDb, conversation_id: str) -> None:
    conn = db.conn
    """Seal the single mutable trailing block for this conversation, if any."""
    conn.execute(
        "UPDATE conversation_blocks SET sealed = 1 WHERE conversation_id = ? AND repository_id = ? AND sealed = 0",
        (conversation_id, db.repository_id),
    )


def _read_block_by_id(
    db: RepoDb,
    conversation_id: str,
    block_id: int | None,
) -> ConversationBlock | None:
    conn = db.conn
    """Read one block row by its id, scoped to a conversation. ``None`` if absent."""
    if block_id is None:
        return None
    row = conn.execute(
        """
        SELECT id, ordinal, kind, payload_json, sealed, service_received_at
          FROM conversation_blocks
         WHERE conversation_id = ? AND repository_id = ? AND id = ?
        """,
        (conversation_id, db.repository_id, block_id),
    ).fetchone()
    if row is None:
        return None
    return ConversationBlock(
        id=int(row["id"]),
        conversation_id=conversation_id,
        ordinal=int(row["ordinal"]),
        kind=str(row["kind"]),
        payload=json.loads(row["payload_json"]),
        sealed=bool(row["sealed"]),
        service_received_at=str(row["service_received_at"]),
    )


def _block_is_live(kind: str, seg: dict[str, Any]) -> bool:
    """Whether a freshly written block stays mutable (sealed = 0).

    Two block kinds are live: an intermediate assistant turn (it grows as the
    pane streams), and an UNANSWERED choice_prompt (the dialog cursor /
    checkboxes move as the user navigates — the chat-input takeover renders
    them from block-updated events, so the row must accept in-place updates
    until the prompt resolves to ``answered``).
    """
    if kind == "assistant_intermediate":
        return True
    return kind == "choice_prompt" and not seg.get("answered")


def append_block(
    db: RepoDb,
    conversation_id: str,
    seg: dict[str, Any],
    *,
    received_at: str | None = None,
    seal_previous: bool = True,
) -> ConversationBlock:
    conn = db.conn
    """Append one block row for ``seg``.

    When ``seal_previous`` is True (the default), any existing live block for
    this conversation is sealed first so the new block becomes the sole live
    trailing block.

    ``assistant_final`` blocks are sealed immediately on insertion (they cannot
    grow further).
    """
    ts = received_at or _now()
    kind = segment_to_block_kind(seg)

    if seal_previous:
        _seal_live_block(db, conversation_id)

    row = conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ord FROM conversation_blocks"
        " WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    ordinal = int(row["next_ord"]) if row is not None else 0

    # Live blocks (see _block_is_live: streaming assistant turns + unanswered
    # choice prompts) stay unsealed for in-place merge updates. Everything else
    # seals immediately.
    sealed = 0 if _block_is_live(kind, seg) else 1

    conn.execute(
        """
        INSERT INTO conversation_blocks
            (repository_id, conversation_id, ordinal, kind, payload_json, sealed, service_received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (db.repository_id, conversation_id, ordinal, kind, json.dumps(seg), sealed, ts),
    )
    row_id = conn.execute("SELECT last_insert_rowid() AS rid").fetchone()["rid"]

    return ConversationBlock(
        id=int(row_id),
        conversation_id=conversation_id,
        ordinal=ordinal,
        kind=kind,
        payload=seg,
        sealed=bool(sealed),
        service_received_at=ts,
    )


def update_live_block(
    db: RepoDb,
    conversation_id: str,
    seg: dict[str, Any],
    *,
    received_at: str | None = None,
) -> bool:
    conn = db.conn
    """Replace the payload of the live trailing block with ``seg``.

    Used when an intermediate assistant turn grows (new text appended) or when
    that turn flips to a terminal kind. The ``received_at`` timestamp on the
    block is refreshed to reflect the latest content. If the new kind is
    terminal (anything other than ``assistant_intermediate``) the block is
    sealed in place per the live-block rule — a block seals when its phase
    flips to final, so a final turn never lingers as a mutable tail. Returns
    True if a live block existed and was updated, False if there is no unsealed
    block (caller should use ``append_block``).
    """
    ts = received_at or _now()
    kind = segment_to_block_kind(seg)
    sealed = 0 if _block_is_live(kind, seg) else 1
    cur = conn.execute(
        """
        SELECT id FROM conversation_blocks
         WHERE conversation_id = ? AND repository_id = ? AND sealed = 0
        """,
        (conversation_id, db.repository_id),
    ).fetchone()
    if cur is None:
        return False
    conn.execute(
        """
        UPDATE conversation_blocks
           SET kind = ?, payload_json = ?, sealed = ?, service_received_at = ?
         WHERE id = ? AND repository_id = ?
        """,
        (kind, json.dumps(seg), sealed, ts, cur["id"], db.repository_id),
    )
    return True


def _sealed_block_can_grow(existing: ConversationBlock, seg: dict[str, Any]) -> bool:
    """True when a sealed assistant final is just a shorter prefix of ``seg``."""
    if not existing.sealed or existing.kind != "assistant_final":
        return False
    if segment_to_block_kind(seg) != existing.kind:
        return False
    old_text = existing.payload.get("text")
    new_text = seg.get("text")
    return (
        isinstance(old_text, str)
        and isinstance(new_text, str)
        and len(new_text) > len(old_text)
        and new_text.startswith(old_text)
    )


def _update_block_by_id(
    db: RepoDb,
    block_id: int | None,
    seg: dict[str, Any],
    *,
    received_at: str | None = None,
) -> ConversationBlock | None:
    conn = db.conn
    if block_id is None:
        return None
    ts = received_at or _now()
    kind = segment_to_block_kind(seg)
    sealed = 0 if _block_is_live(kind, seg) else 1
    conn.execute(
        """
        UPDATE conversation_blocks
           SET kind = ?, payload_json = ?, sealed = ?, service_received_at = ?
         WHERE id = ? AND repository_id = ?
        """,
        (kind, json.dumps(seg), sealed, ts, block_id, db.repository_id),
    )
    row = conn.execute(
        """
        SELECT conversation_id, ordinal, kind, payload_json, sealed, service_received_at
          FROM conversation_blocks
         WHERE id = ? AND repository_id = ?
        """,
        (block_id, db.repository_id),
    ).fetchone()
    if row is None:
        return None
    return ConversationBlock(
        id=block_id,
        conversation_id=str(row["conversation_id"]),
        ordinal=int(row["ordinal"]),
        kind=str(row["kind"]),
        payload=json.loads(row["payload_json"]),
        sealed=bool(row["sealed"]),
        service_received_at=str(row["service_received_at"]),
    )


def read_conversation_blocks(
    db: RepoDb,
    conversation_id: str,
) -> list[ConversationBlock]:
    conn = db.conn
    """Return all blocks for a conversation in ordinal order."""
    rows = conn.execute(
        """
        SELECT id, ordinal, kind, payload_json, sealed, service_received_at
          FROM conversation_blocks
         WHERE conversation_id = ? AND repository_id = ?
         ORDER BY ordinal
        """,
        (conversation_id, db.repository_id),
    ).fetchall()
    return [
        ConversationBlock(
            id=int(r["id"]),
            conversation_id=conversation_id,
            ordinal=int(r["ordinal"]),
            kind=r["kind"],
            payload=json.loads(r["payload_json"]),
            sealed=bool(r["sealed"]),
            service_received_at=r["service_received_at"],
        )
        for r in rows
    ]


def _restore_parsed_turn_order(
    db: RepoDb,
    conversation_id: str,
    parsed_segments: Sequence[dict[str, Any]],
) -> dict[int, int]:
    conn = db.conn
    """Restore user/assistant interleaving without trusting parsed user payloads.

    User rows are authoritative and are written at the send boundary. Parsed
    user segments are therefore used only as exact-text position anchors. Their
    payloads never enter storage. This repairs the case where several user rows
    land before a cumulative harness projection appends their assistant replies.

    Returns changed block-id → ordinal mappings so already-built change objects
    can carry the corrected position.
    """
    blocks = read_conversation_blocks(db, conversation_id)
    users = [block for block in blocks if block.kind == "user"]
    # Notices are service-injected and have no parsed-segment counterpart.
    non_users = [block for block in blocks if block.kind not in {"user", "notice"}]
    if not users or not non_users:
        return {}

    unused_users = list(users)
    non_user_idx = 0
    ordered_ids: list[int] = []
    matched_user = False
    for segment in parsed_segments:
        if segment.get("type") == "user":
            text = segment.get("text")
            if not isinstance(text, str):
                continue
            body = text.strip()
            match_idx = next(
                (
                    idx
                    for idx, block in enumerate(unused_users)
                    if str(block.payload.get("text", "")).strip() == body
                ),
                None,
            )
            if match_idx is None:
                continue
            block = unused_users.pop(match_idx)
            if block.id is not None:
                ordered_ids.append(block.id)
                matched_user = True
            continue
        if non_user_idx < len(non_users):
            block = non_users[non_user_idx]
            non_user_idx += 1
            if block.id is not None:
                ordered_ids.append(block.id)

    # With no trustworthy user anchor, preserve the established authoritative
    # order rather than letting parser noise reposition user content.
    if not matched_user:
        return {}
    placed = set(ordered_ids)
    ordered_ids.extend(
        block.id for block in blocks if block.id is not None and block.id not in placed
    )
    current_ids = [block.id for block in blocks if block.id is not None]
    if ordered_ids == current_ids:
        return {}

    offset = len(blocks) + 1
    conn.execute(
        "UPDATE conversation_blocks SET ordinal = ordinal + ? "
        "WHERE conversation_id = ? AND repository_id = ?",
        (offset, conversation_id, db.repository_id),
    )
    changed: dict[int, int] = {}
    for ordinal, block_id in enumerate(ordered_ids):
        conn.execute(
            "UPDATE conversation_blocks SET ordinal = ? WHERE id = ? AND repository_id = ?",
            (ordinal, block_id, db.repository_id),
        )
        changed[block_id] = ordinal
    return changed


def read_user_texts(db: RepoDb, conversation_id: str) -> list[str]:
    """Return the text of every ground-truth ``user`` block, in order.

    These are recorded authoritatively at the send boundary, so they are the
    canonical record of what the user typed. Markerless grammars use them as
    anchors to recognise (and drop) user turns echoed back in the pane.
    """
    return [
        text
        for b in read_conversation_blocks(db, conversation_id)
        if b.kind == "user" and (text := str(b.payload.get("text", "")).strip())
    ]


def read_conversation_doc(
    db: RepoDb,
    conversation_id: str,
) -> dict[str, Any] | None:
    conn = db.conn
    """Reconstruct a ``TranscriptDoc`` dict from stored blocks.

    Returns ``None`` if the conversation_id is not found.

    The returned dict has the shape::

        {"harness": ..., "state": ..., "condensed": None, "segments": [...]}

    which matches the output of ``TranscriptAccumulator.to_dict()``. The
    ``condensed`` key is always ``None`` here — rolling chunk summaries live in
    conversation_chunk_summaries, read via ``read_chunk_summaries``.
    """
    conv_row = conn.execute(
        "SELECT harness, live_state FROM conversations "
        "WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    if conv_row is None:
        return None

    blocks = read_conversation_blocks(db, conversation_id)
    segments = [b.payload for b in blocks]
    return {
        "harness": conv_row["harness"],
        "state": conv_row["live_state"],
        "condensed": None,
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Condensed-view chunk summaries (TUIchat Phase 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkSummary:
    """One rolling chunk summary plus the block ids it attributes to."""

    summary_id: int
    conversation_id: str
    chunk_idx: int
    summary: str
    block_ids: tuple[int, ...]
    created_at: str


def write_chunk_summary(
    db: RepoDb,
    conversation_id: str,
    *,
    summary: str,
    block_ids: Sequence[int],
    created_at: str | None = None,
) -> int:
    conn = db.conn
    """Append one chunk summary + its attribution pointers. Return summary_id.

    ``chunk_idx`` is assigned as the next index for the conversation (ordered
    append). ``block_ids`` are explicit pointers into ``conversation_blocks.id``
    — the attribution contract (not implicit ordinal ranges). An empty/blank
    ``summary`` is a programming error here: callers must apply the
    empty-summary guard (degrade to Verbose) before writing.
    """
    text = (summary or "").strip()
    if not text:
        raise ValueError("do not persist an empty chunk summary")
    ts = created_at or _now()
    row = conn.execute(
        "SELECT COALESCE(MAX(chunk_idx) + 1, 0) AS next_idx"
        " FROM conversation_chunk_summaries "
        "WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    chunk_idx = int(row["next_idx"])
    cur = conn.execute(
        "INSERT INTO conversation_chunk_summaries"
        " (repository_id, conversation_id, chunk_idx, summary, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (db.repository_id, conversation_id, chunk_idx, text, ts),
    )
    summary_id = int(cur.lastrowid)
    # De-dup + preserve order of explicit block-id pointers.
    seen: set[int] = set()
    for bid in block_ids:
        if bid in seen:
            continue
        seen.add(bid)
        conn.execute(
            "INSERT OR IGNORE INTO chunk_summary_blocks "
            "(repository_id, summary_id, block_id) VALUES (?, ?, ?)",
            (db.repository_id, summary_id, int(bid)),
        )
    _invalidate_conversations(db, conversation_id)
    return summary_id


def read_chunk_summaries(
    db: RepoDb,
    conversation_id: str,
) -> list[ChunkSummary]:
    conn = db.conn
    """Read all chunk summaries for a conversation, ordered by chunk_idx."""
    rows = conn.execute(
        "SELECT summary_id, chunk_idx, summary, created_at"
        " FROM conversation_chunk_summaries"
        " WHERE conversation_id = ? AND repository_id = ? ORDER BY chunk_idx",
        (conversation_id, db.repository_id),
    ).fetchall()
    if not rows:
        return []
    block_rows = conn.execute(
        "SELECT csb.summary_id AS summary_id, csb.block_id AS block_id"
        " FROM chunk_summary_blocks csb"
        " JOIN conversation_chunk_summaries ccs ON ccs.summary_id = csb.summary_id"
        " AND ccs.repository_id = csb.repository_id"
        " WHERE ccs.conversation_id = ? AND ccs.repository_id = ?"
        " ORDER BY csb.summary_id, csb.block_id",
        (conversation_id, db.repository_id),
    ).fetchall()
    blocks_by_summary: dict[int, list[int]] = {}
    for br in block_rows:
        blocks_by_summary.setdefault(int(br["summary_id"]), []).append(int(br["block_id"]))
    return [
        ChunkSummary(
            summary_id=int(r["summary_id"]),
            conversation_id=conversation_id,
            chunk_idx=int(r["chunk_idx"]),
            summary=str(r["summary"]),
            block_ids=tuple(blocks_by_summary.get(int(r["summary_id"]), ())),
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Merge: reconcile a freshly parsed TranscriptDoc against stored blocks
# ---------------------------------------------------------------------------


def merge_conversation_doc(  # noqa: PLR0912 - closed merge-state branches
    db: RepoDb,
    conversation_id: str,
    doc: dict[str, Any],
    *,
    received_at: str | None = None,
) -> list[ConversationBlock]:
    """Reconcile a freshly parsed ``doc`` against stored blocks.

    Decision rule (mirrors merge_transcript's longer-replaces-shorter logic at
    block granularity):

    - If the doc has *more* segments than stored blocks, apply the full doc:
      seal all existing sealed blocks, update the live tail in-place if the last
      segment matches (kind) the live block, append any new segments.
    - If the doc has *equal* count and the content differs (last block grew),
      update the live tail in-place (kind must be ``assistant_intermediate``).
    - If the doc has *fewer* segments, treat it as transient pane noise: ignore.

    Also updates the ``conversations`` row with the latest harness/state.

    Returns the effective block list after merging.
    """
    ts = received_at or _now()
    segments: list[dict[str, Any]] = doc.get("segments", [])

    # Refresh conversation metadata from this parse.
    upsert_conversation(
        db,
        conversation_id=conversation_id,
        agent_id=_get_agent_id(db, conversation_id),
        harness=doc.get("harness"),
        live_state=doc.get("state"),
    )

    stored = read_conversation_blocks(db, conversation_id)

    if not segments:
        return stored

    n_stored = len(stored)
    n_parsed = len(segments)

    if n_parsed < n_stored:
        # Shorter parse: transient pane noise, ignore.
        return stored

    if n_parsed == n_stored:
        # Same count: only valid update is the live tail growing.
        if not stored:
            return stored
        live = stored[-1]
        if not live.sealed:
            new_kind = segment_to_block_kind(segments[-1])
            new_payload = segments[-1]
            if new_payload != live.payload:
                update_live_block(db, conversation_id, new_payload, received_at=ts)
                # Refresh the local object so callers see updated state.
                stored[-1] = ConversationBlock(
                    id=live.id,
                    conversation_id=conversation_id,
                    ordinal=live.ordinal,
                    kind=new_kind,
                    payload=new_payload,
                    sealed=new_kind != "assistant_intermediate",
                    service_received_at=ts,
                )
        elif _sealed_block_can_grow(live, segments[-1]):
            _update_block_by_id(db, live.id, segments[-1], received_at=ts)
        return read_conversation_blocks(db, conversation_id)

    # n_parsed > n_stored: apply the longer parse.
    # Reuse stored sealed history. Update/append from the divergence point.
    # All previously stored sealed blocks are kept as-is.
    # For blocks already stored, update the live tail if content changed.
    result = list(stored)
    for i, seg in enumerate(segments):
        if i < n_stored:
            existing = stored[i]
            if not existing.sealed:
                # Live trailing block — update in place if content changed.
                if seg != existing.payload:
                    update_live_block(db, conversation_id, seg, received_at=ts)
                    new_kind = segment_to_block_kind(seg)
                    result[i] = ConversationBlock(
                        id=existing.id,
                        conversation_id=conversation_id,
                        ordinal=existing.ordinal,
                        kind=new_kind,
                        payload=seg,
                        sealed=new_kind != "assistant_intermediate",
                        service_received_at=ts,
                    )
            elif _sealed_block_can_grow(existing, seg):
                updated = _update_block_by_id(db, existing.id, seg, received_at=ts)
                if updated is not None:
                    result[i] = updated
            # Other sealed blocks are immutable — leave them as-is.
        else:
            # New segment beyond stored history: seal previous live block,
            # then append.
            new_block = append_block(db, conversation_id, seg, received_at=ts, seal_previous=True)
            result.append(new_block)

    return read_conversation_blocks(db, conversation_id)


def merge_non_user_segments(
    db: RepoDb,
    conversation_id: str,
    segments: list[dict[str, Any]],
    *,
    received_at: str | None = None,
) -> list[ConversationBlock]:
    blocks, _changes = merge_non_user_segments_with_changes(
        db,
        conversation_id,
        segments,
        received_at=received_at,
    )
    return blocks


def merge_non_user_segments_with_changes(
    db: RepoDb,
    conversation_id: str,
    segments: list[dict[str, Any]],
    *,
    received_at: str | None = None,
) -> tuple[list[ConversationBlock], list[ConversationBlockChange]]:
    conn = db.conn
    """Reconcile parsed *non-user* segments against stored non-user blocks.

    This is the projector-side sibling of :func:`merge_conversation_doc`. The
    difference is what the parsed stream is reconciled against: ground-truth
    ``user`` blocks (recorded authoritatively at the send boundary, 1.c) are
    interleaved into the stored stream, but the parsed doc has its re-derived
    ``user`` segments stripped. Reconciling the stripped parse against the
    *full* interleaved stored stream would make every parse look shorter than
    storage and get dropped as pane noise. So we project storage to its
    non-user blocks first and apply the same longer-replaces-shorter rule
    against that subsequence, mapping decisions back to the real block rows.

    Correctness against the live-block rule: user blocks are always sealed, so
    the single ever-unsealed block (if any) is the global trailing block, which
    is necessarily ``non_user[-1]``. ``update_live_block`` targets that one
    ``sealed=0`` row. New segments append after every stored block (including a
    trailing sealed user block) via ``append_block``'s ``MAX(ordinal)+1``.
    ``seal_previous=True`` is a no-op when the tail is an already-sealed user
    block.

    Returns the effective block list after merging.
    """
    ts = received_at or _now()
    if not segments:
        return read_conversation_blocks(db, conversation_id), []

    stored = read_conversation_blocks(db, conversation_id)
    non_user = [b for b in stored if b.kind != "user"]
    n_stored = len(non_user)
    n_parsed = len(segments)
    changes: list[ConversationBlockChange] = []

    if n_parsed < n_stored:
        # Shorter parse: transient pane noise, ignore.
        return stored, []

    for i in range(n_stored):
        existing = non_user[i]
        if not existing.sealed and segments[i] != existing.payload:
            # Live trailing block grew or flipped terminal — update in place.
            update_live_block(db, conversation_id, segments[i], received_at=ts)
            row = conn.execute(
                """
                SELECT id, ordinal, kind, payload_json, sealed, service_received_at
                  FROM conversation_blocks
                 WHERE id = ? AND repository_id = ?
                """,
                (existing.id, db.repository_id),
            ).fetchone()
            if row is not None:
                changes.append(
                    ConversationBlockChange(
                        action="block-updated",
                        block=ConversationBlock(
                            id=int(row["id"]),
                            conversation_id=conversation_id,
                            ordinal=int(row["ordinal"]),
                            kind=row["kind"],
                            payload=json.loads(row["payload_json"]),
                            sealed=bool(row["sealed"]),
                            service_received_at=row["service_received_at"],
                        ),
                        previous_payload=dict(existing.payload),
                        previous_sealed=existing.sealed,
                    )
                )
        elif _sealed_block_can_grow(existing, segments[i]):
            updated = _update_block_by_id(db, existing.id, segments[i], received_at=ts)
            if updated is not None:
                changes.append(
                    ConversationBlockChange(
                        action="block-updated",
                        block=updated,
                        previous_payload=dict(existing.payload),
                        previous_sealed=existing.sealed,
                    )
                )
        # Other sealed blocks are immutable — leave them as-is.

    # When new segments are about to be appended, the first append seals the
    # current live trailing block (a streaming ``assistant_intermediate`` or an
    # unanswered ``choice_prompt``) via a silent UPDATE inside ``append_block``.
    # That seal transition carries no change of its own, so downstream consumers
    # that key off ``block.sealed`` (notably the producer's condensed-view
    # summarization buffer, which only buffers SEALED intermediate blocks) would
    # never observe the block as sealed and would skip it forever — leaving its
    # prose un-summarized and rendered verbatim in Condensed. Emit an explicit
    # ``block-updated`` for the now-sealed predecessor so the seal is observable.
    if n_parsed > n_stored:
        live_pred = non_user[-1] if non_user and not non_user[-1].sealed else None
        for i in range(n_stored, n_parsed):
            block = append_block(
                db, conversation_id, segments[i], received_at=ts, seal_previous=True
            )
            if live_pred is not None and i == n_stored:
                sealed_pred = _read_block_by_id(db, conversation_id, live_pred.id)
                if sealed_pred is not None and sealed_pred.sealed:
                    changes.append(
                        ConversationBlockChange(
                            action="block-updated",
                            block=sealed_pred,
                            previous_payload=dict(live_pred.payload),
                            previous_sealed=live_pred.sealed,
                        )
                    )
            changes.append(ConversationBlockChange(action="block-appended", block=block))

    return read_conversation_blocks(db, conversation_id), changes


def _get_agent_id(db: RepoDb, conversation_id: str) -> str:
    conn = db.conn
    """Return the agent_id for an existing conversation, or empty string."""
    row = conn.execute(
        "SELECT agent_id FROM conversations WHERE conversation_id = ? AND repository_id = ?",
        (conversation_id, db.repository_id),
    ).fetchone()
    return str(row["agent_id"]) if row else ""


# ---------------------------------------------------------------------------
# Ground-truth user blocks + server-side projection (Phase 1.c)
# ---------------------------------------------------------------------------


def append_user_message(
    db: RepoDb,
    agent_id: str,
    text: str,
    *,
    conversation_id: str | None = None,
    received_at: str | None = None,
    client_message_id: str | None = None,
) -> ConversationBlock | None:
    """Record a ground-truth user turn at the send boundary.

    The service *knows* the exact text the user typed, so it stores it
    authoritatively instead of re-deriving it from a noisy pane capture
    (which is what corrupts the collaborator chat). Writes to *both* stores:

    - the 1.b JSON conversation store as a sealed ``user`` block, and
    - the legacy flat ``agent_messages`` log for compatibility.

    The conversation row is upserted on first use so the block always has a
    home. ``conversation_id`` defaults to ``agent_id`` (one live conversation
    per agent). No-op for blank text.

    ``client_message_id`` is an optional end-to-end correlation id from the
    client (optimistic shadow turns). When present it is stored on the user
    segment payload so the UI can drop matching pending items once the
    authoritative snapshot arrives.
    """
    body = (text or "").strip()
    if not body:
        return None
    conv_id = conversation_id or agent_id
    ts = received_at or _now()
    upsert_conversation(db, conversation_id=conv_id, agent_id=agent_id)
    payload: dict[str, Any] = {"type": "user", "text": body}
    if isinstance(client_message_id, str) and client_message_id.strip():
        payload["client_message_id"] = client_message_id.strip()
    block = append_block(db, conv_id, payload, received_at=ts)
    append_agent_message(db, agent_id, "user", body, captured_at=ts)
    _invalidate_conversations(db, conv_id)
    return block


def append_notice(
    db: RepoDb,
    agent_id: str,
    message: str,
    *,
    severity: str = "error",
    conversation_id: str | None = None,
    received_at: str | None = None,
) -> ConversationBlock | None:
    """Record a service-originated notice in the conversation stream.

    Notices are how startup/usage-limit failures become visible chat history
    instead of disappearing into worker errors. They intentionally do not write
    to the legacy flat ``agent_messages`` table because notices are structured
    UI blocks, not assistant/user turns.
    """
    body = (message or "").strip()
    if not body:
        return None
    conv_id = conversation_id or agent_id
    ts = received_at or _now()
    upsert_conversation(db, conversation_id=conv_id, agent_id=agent_id)
    block = append_block(
        db,
        conv_id,
        {"type": "notice", "severity": severity, "message": body},
        received_at=ts,
    )
    _invalidate_conversations(db, conv_id)
    return block


def _doc_to_flat_turns(doc: dict[str, Any]) -> list[Turn]:
    """Project a TranscriptDoc's segments into flat ``(role, text)`` turns.

    Only ``user`` and ``assistant`` segments carry into the flat log. Tool /
    plan / event segments live only in the JSON store.
    """
    turns: list[Turn] = []
    for seg in doc.get("segments", []):
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type")
        if seg_type in ("user", "assistant"):
            text = seg.get("text")
            if isinstance(text, str) and text.strip():
                turns.append(("user" if seg_type == "user" else "assistant", text))
    return turns


def project_parsed_doc(
    db: RepoDb,
    agent_id: str,
    doc: dict[str, Any],
    *,
    conversation_id: str | None = None,
    received_at: str | None = None,
) -> dict[str, Any]:
    merged, _changes = project_parsed_doc_with_changes(
        db,
        agent_id,
        doc,
        conversation_id=conversation_id,
        received_at=received_at,
    )
    return merged


def project_parsed_doc_with_changes(
    db: RepoDb,
    agent_id: str,
    doc: dict[str, Any],
    *,
    conversation_id: str | None = None,
    received_at: str | None = None,
) -> tuple[dict[str, Any], list[ConversationBlockChange]]:
    conn = db.conn
    """Reconcile a freshly parsed pane ``doc`` into the unified stores.

    The send boundary already recorded authoritative ``user`` blocks, so the
    parser's own (re-derived, noisy) ``user`` segments are *stripped* before
    merge — ground-truth user blocks are the single source of user turns,
    uniform across echoing (CC) and markerless (codex) harnesses. The
    remaining non-user segments reconcile into the JSON store via
    :func:`merge_non_user_segments`, which projects storage to its non-user
    blocks first so the stripped parse and stored stream are the same shape
    (reconciling against the full interleaved stream would drop every parse as
    pane noise).

    The flat ``agent_messages`` log is then rebuilt from the merged JSON store
    so both stores stay consistent and user turns never duplicate.

    Returns the reconstructed conversation doc plus the concrete block changes
    that should be pushed over the bus.
    """
    conv_id = conversation_id or agent_id
    segments = doc.get("segments", [])
    non_user = [s for s in segments if not (isinstance(s, dict) and s.get("type") == "user")]
    previous_metadata = conn.execute(
        "SELECT harness, live_state, status FROM conversations "
        "WHERE conversation_id = ? AND repository_id = ?",
        (conv_id, db.repository_id),
    ).fetchone()
    upsert_conversation(
        db,
        conversation_id=conv_id,
        agent_id=agent_id,
        harness=doc.get("harness"),
        live_state=doc.get("state"),
    )
    _blocks, changes = merge_non_user_segments_with_changes(
        db,
        conv_id,
        non_user,
        received_at=received_at,
    )
    reordered = _restore_parsed_turn_order(db, conv_id, segments)
    for change in changes:
        if change.block.id in reordered:
            change.block.ordinal = reordered[change.block.id]

    merged = read_conversation_doc(db, conv_id) or {"segments": []}
    replace_agent_messages(db, agent_id, _doc_to_flat_turns(merged), captured_at=received_at)
    metadata_changed = (
        previous_metadata is None
        or (doc.get("harness") is not None and doc.get("harness") != previous_metadata["harness"])
        or (doc.get("state") is not None and doc.get("state") != previous_metadata["live_state"])
        or previous_metadata["status"] != "in_progress"
    )
    if changes or metadata_changed or reordered:
        _invalidate_conversations(db, conv_id)
    return merged, changes


def mark_stale_conversations(db: RepoDb) -> int:
    conn = db.conn
    """Flip all in_progress conversations to stale.

    Called during startup reconciliation (1.g) — any conversation left
    in_progress from a prior run has no live pane. Mark it stale. Also
    clears ``queued_message``: graceful stop clears it in on_session_end,
    but a SIGKILL bypasses that path and would leave a stale queued badge
    in the TUI after restart.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        "UPDATE conversations SET status = 'stale', queued_message = NULL,"
        " updated_at = ? WHERE repository_id = ? AND status = 'in_progress'",
        (_now(), db.repository_id),
    )
    if cur.rowcount:
        _invalidate_conversations(db, "*")
    return int(cur.rowcount)
