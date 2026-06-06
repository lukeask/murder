"""Persisted user↔agent conversation logs.

Two storage paths live side-by-side in this module:

**Legacy flat-turns path** (``merge_transcript``, ``read_conversation``, ``clear``):
  An interactive harness renders its chat in a tmux pane; ``parse_transcript``
  turns a pane capture into an ordered list of ``(role, text)`` turns.
  ``merge_transcript`` reconciles a fresh parse against what's already persisted
  so the stored log is the longest, most complete transcript observed.
  Still used for compatibility; see ``agent_messages`` table.

**JSON conversation-block path** (Phase 1.b):
  Parsed segment dicts are stored block-per-row in ``conversation_blocks``
  with full payload JSON, service-received timestamps, and a "live block" rule:
  at most one trailing ``sealed=0`` row per conversation at any time.
  A ``conversations`` row holds metadata per conversation session.
  See :func:`merge_conversation_doc`, :func:`read_conversation_blocks`, etc.

Both paths are independent; 1.c will wire the JSON path into the live app.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from murder.state.persistence.agents import get_agent_messages, replace_agent_messages

# ---------------------------------------------------------------------------
# Legacy flat-turns path (agent_messages table) — unchanged
# ---------------------------------------------------------------------------

# (role, text) — deliberately a plain tuple for UI transcript rendering.
Turn = tuple[str, str]


def read_conversation(conn: sqlite3.Connection, agent_id: str) -> list[Turn]:
    return [(r["role"], r["body"]) for r in get_agent_messages(conn, agent_id)]


def clear(conn: sqlite3.Connection, agent_id: str) -> None:
    """Drop the persisted log for ``agent_id`` — call when a fresh agent
    session starts, so a new run doesn't show the previous run's chat."""
    replace_agent_messages(conn, agent_id, [])


def merge_transcript(
    conn: sqlite3.Connection,
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
    stored = read_conversation(conn, agent_id)
    if not parsed:
        return stored
    if len(parsed) > len(stored) or (len(parsed) == len(stored) and parsed != stored):
        replace_agent_messages(conn, agent_id, parsed, captured_at=captured_at)
        return parsed
    return stored


# ---------------------------------------------------------------------------
# JSON conversation-block path (conversations + conversation_blocks tables)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# Canonical set of block kinds.  assistant segment is split by phase so each
# kind maps unambiguously back to a segment dict stored in payload_json.
# `notice` is reserved for 1.f (usage/error notices; no parser emits it yet).
BLOCK_KINDS: tuple[str, ...] = (
    "user",
    "assistant_intermediate",
    "assistant_final",
    "tool_call",
    "plan_update",
    "agent_event",
    "choice_prompt",
    "notice",                # reserved for 1.f
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
    return seg_type


@dataclass
class ConversationBlock:
    """In-memory representation of a single conversation_blocks row."""

    id: int | None          # None before first DB write
    conversation_id: str
    ordinal: int
    kind: str
    payload: dict[str, Any]  # the original segment dict, lossless
    sealed: bool
    service_received_at: str


# ---------------------------------------------------------------------------
# Conversation-level CRUD
# ---------------------------------------------------------------------------

def upsert_conversation(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    agent_id: str,
    harness: str | None = None,
    model: str | None = None,
    harness_session_id: str | None = None,
    live_state: str | None = None,
    condensed: str | None = None,
    status: str = "in_progress",
) -> None:
    """Insert or update the metadata row for a conversation session."""
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO conversations
                (conversation_id, agent_id, harness, model, harness_session_id,
                 live_state, condensed, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id, agent_id, harness, model, harness_session_id,
                live_state, condensed, status, now, now,
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
                   condensed          = COALESCE(?, condensed),
                   status             = ?,
                   updated_at         = ?
             WHERE conversation_id = ?
            """,
            (
                harness, model, harness_session_id, live_state, condensed,
                status, now, conversation_id,
            ),
        )


def set_conversation_status(
    conn: sqlite3.Connection,
    conversation_id: str,
    status: str,
) -> None:
    """Transition conversation status (in_progress → complete | stale)."""
    conn.execute(
        "UPDATE conversations SET status = ?, updated_at = ? WHERE conversation_id = ?",
        (status, _now(), conversation_id),
    )


def set_harness_session_id(
    conn: sqlite3.Connection,
    conversation_id: str,
    harness_session_id: str,
) -> None:
    """Record the resume session id captured from the harness on graceful exit."""
    conn.execute(
        """
        UPDATE conversations
           SET harness_session_id = ?, updated_at = ?
         WHERE conversation_id = ?
        """,
        (harness_session_id, _now(), conversation_id),
    )


# ---------------------------------------------------------------------------
# Block-level persistence
# ---------------------------------------------------------------------------

def _seal_live_block(conn: sqlite3.Connection, conversation_id: str) -> None:
    """Seal the single mutable trailing block for this conversation, if any."""
    conn.execute(
        "UPDATE conversation_blocks SET sealed = 1 WHERE conversation_id = ? AND sealed = 0",
        (conversation_id,),
    )


def append_block(
    conn: sqlite3.Connection,
    conversation_id: str,
    seg: dict[str, Any],
    *,
    received_at: str | None = None,
    seal_previous: bool = True,
) -> ConversationBlock:
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
        _seal_live_block(conn, conversation_id)

    row = conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next_ord FROM conversation_blocks"
        " WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    ordinal = int(row["next_ord"]) if row is not None else 0

    # final-phase assistant blocks and all non-assistant blocks seal immediately;
    # intermediate assistant blocks stay live (may be updated in place by merge).
    sealed = 0 if kind == "assistant_intermediate" else 1

    conn.execute(
        """
        INSERT INTO conversation_blocks
            (conversation_id, ordinal, kind, payload_json, sealed, service_received_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (conversation_id, ordinal, kind, json.dumps(seg), sealed, ts),
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
    conn: sqlite3.Connection,
    conversation_id: str,
    seg: dict[str, Any],
    *,
    received_at: str | None = None,
) -> bool:
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
    sealed = 0 if kind == "assistant_intermediate" else 1
    cur = conn.execute(
        """
        SELECT id FROM conversation_blocks
         WHERE conversation_id = ? AND sealed = 0
        """,
        (conversation_id,),
    ).fetchone()
    if cur is None:
        return False
    conn.execute(
        """
        UPDATE conversation_blocks
           SET kind = ?, payload_json = ?, sealed = ?, service_received_at = ?
         WHERE id = ?
        """,
        (kind, json.dumps(seg), sealed, ts, cur["id"]),
    )
    return True


def read_conversation_blocks(
    conn: sqlite3.Connection,
    conversation_id: str,
) -> list[ConversationBlock]:
    """Return all blocks for a conversation in ordinal order."""
    rows = conn.execute(
        """
        SELECT id, ordinal, kind, payload_json, sealed, service_received_at
          FROM conversation_blocks
         WHERE conversation_id = ?
         ORDER BY ordinal
        """,
        (conversation_id,),
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


def read_conversation_doc(
    conn: sqlite3.Connection,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Reconstruct a ``TranscriptDoc`` dict from stored blocks.

    Returns ``None`` if the conversation_id is not found.

    The returned dict has the shape::

        {"harness": ..., "state": ..., "condensed": ..., "segments": [...]}

    which matches the output of ``TranscriptAccumulator.to_dict()``.
    """
    conv_row = conn.execute(
        "SELECT harness, live_state, condensed FROM conversations"
        " WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if conv_row is None:
        return None

    blocks = read_conversation_blocks(conn, conversation_id)
    segments = [b.payload for b in blocks]
    return {
        "harness": conv_row["harness"],
        "state": conv_row["live_state"],
        "condensed": conv_row["condensed"],
        "segments": segments,
    }


# ---------------------------------------------------------------------------
# Merge: reconcile a freshly parsed TranscriptDoc against stored blocks
# ---------------------------------------------------------------------------

def merge_conversation_doc(
    conn: sqlite3.Connection,
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

    Also updates the ``conversations`` row with the latest harness/state/condensed.

    Returns the effective block list after merging.
    """
    ts = received_at or _now()
    segments: list[dict[str, Any]] = doc.get("segments", [])

    # Refresh conversation metadata from this parse.
    upsert_conversation(
        conn,
        conversation_id=conversation_id,
        agent_id=_get_agent_id(conn, conversation_id),
        harness=doc.get("harness"),
        live_state=doc.get("state"),
        condensed=doc.get("condensed"),
    )

    stored = read_conversation_blocks(conn, conversation_id)

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
                update_live_block(conn, conversation_id, new_payload, received_at=ts)
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
        return read_conversation_blocks(conn, conversation_id)

    # n_parsed > n_stored: apply the longer parse.
    # Reuse stored sealed history; update/append from the divergence point.
    # All previously stored sealed blocks are kept as-is.
    # For blocks already stored, update the live tail if content changed.
    result = list(stored)
    for i, seg in enumerate(segments):
        if i < n_stored:
            existing = stored[i]
            if not existing.sealed:
                # Live trailing block — update in place if content changed.
                if seg != existing.payload:
                    update_live_block(conn, conversation_id, seg, received_at=ts)
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
            # Sealed blocks: immutable — leave them as-is.
        else:
            # New segment beyond stored history: seal previous live block,
            # then append.
            new_block = append_block(
                conn, conversation_id, seg, received_at=ts, seal_previous=True
            )
            result.append(new_block)

    return read_conversation_blocks(conn, conversation_id)


def _get_agent_id(conn: sqlite3.Connection, conversation_id: str) -> str:
    """Return the agent_id for an existing conversation, or empty string."""
    row = conn.execute(
        "SELECT agent_id FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return str(row["agent_id"]) if row else ""


def mark_stale_conversations(conn: sqlite3.Connection) -> int:
    """Flip all in_progress conversations to stale.

    Called during startup reconciliation (1.g) — any conversation left
    in_progress from a prior run has no live pane; mark it stale.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        "UPDATE conversations SET status = 'stale', updated_at = ?"
        " WHERE status = 'in_progress'",
        (_now(),),
    )
    return cur.rowcount
