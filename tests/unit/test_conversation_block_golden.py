"""Cross-language contract anchor for the `conversation.block` wire shape (F11 H3).

`conversation.block` is the one deliberate content-bearing exception to murder's
key-only bus contract, so the inner ``block`` wire shape (produced by
:func:`block_to_wire`) is a *real* contract between the Python producer and the
Ink TUI consumer (``conversationsSlice.parseBlock`` + ``conversationsSelectors``).

This test pins that contract from the **Python side**: it rebuilds one
representative transcript covering every block ``kind`` by running real segment
dicts (the ground-truth schema, see ``tests/fixtures/transcripts/SCHEMA.md``)
through the *real* producer path —
``append_block`` / ``update_live_block`` → ``block_to_wire`` — and asserts the
result still equals the committed golden fixture that the Ink contract test
also consumes.

Drift on EITHER side breaks a test:
  - If a Python field name/type changes (e.g. ``kind`` → ``type``, ``id`` int →
    str, ``payload`` flattened), the regenerated wire no longer matches the
    committed golden → **this test fails**.
  - If the Ink consumer starts reading a key the producer doesn't emit, the
    Ink contract test (``inktui/test/store/conversations/conversationBlockContract.test.ts``)
    fails against the same golden.

The golden lives under the Ink test tree
(``inktui/test/fixtures/conversation-block-golden.json``) so the TS test can
import it directly; this test is the Python anchor for it.

Regenerate after an *intentional* shape change with::

    REGEN_GOLDEN=1 python -m pytest tests/unit/test_conversation_block_golden.py -q
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from murder.state.persistence.conversation import (
    append_block,
    block_to_wire,
    update_live_block,
    upsert_conversation,
)
from murder.state.persistence.schema import get_db, init_db

# The golden is committed under the Ink test tree (the TS contract test imports it).
GOLDEN_PATH = (
    Path(__file__).parent.parent.parent
    / "inktui"
    / "test"
    / "fixtures"
    / "conversation-block-golden.json"
)

AGENT_ID = "crow-7"  # conversation_id == agent_id (1:1, see ConversationProducer)

# A fixed timestamp so the golden is deterministic (block_to_wire passes
# service_received_at straight through).
TS = "2026-06-09T00:00:00"


# ---------------------------------------------------------------------------
# Representative segments — one per block kind, field names verbatim from
# tests/fixtures/transcripts/SCHEMA.md (the parser ground-truth schema).
# Payload-internal field names are CROSS-CHECKED against REAL parser output (so the
# anchor covers the payload the Ink selectors actually read, not just the wire envelope):
#   - choice_prompt: tests/fixtures/transcripts/cc_mc_answered/expected.json (answered:true,
#     chosen:int, selected, footer, options[].number/label/description) and
#     cc_mc_awaiting_approval/expected.json (answered:false live prompt).
#   - agent_event: tests/fixtures/transcripts/cc/expected.json
#     ({name, status:'dispatched'|'completed', elapsed:str|null}).
# These were verified field-for-field as of F11 H3; if the parser renames a segment field, update
# both the golden here AND the Ink selector that reads it.
# Ordered so the LAST block is an *unanswered* choice_prompt (live prompt) and
# an *answered* choice_prompt sits mid-transcript — this single ordered
# transcript exercises the Ink trailing-segment `isLivePrompt` heuristic on
# both sides (live = unanswered AND trailing; answered-mid = not live).
# ---------------------------------------------------------------------------

# (action, segment) pairs. "append" → append_block; "update" → update_live_block
# (the live trailing assistant_intermediate growing in place — the path that
# emits `block-updated`).
_SEGMENTS: list[tuple[str, dict[str, Any]]] = [
    ("append", {"type": "user", "text": "build the thing"}),
    # assistant_intermediate stays live, then grows via update_live_block →
    # this is the `block-updated` replace-by-id case (numeric id must round-trip).
    (
        "append",
        {"type": "assistant", "phase": "intermediate", "text": "Sure, starting", "elapsed": None},
    ),
    (
        "update",
        {
            "type": "assistant",
            "phase": "intermediate",
            "text": "Sure, starting — reading files",
            "elapsed": None,
        },
    ),
    (
        "append",
        {
            "type": "tool_call",
            "title": "Bash",
            "input": "ls -la",
            "result": "total 0",
            "elided": True,
            "running": False,
        },
    ),
    (
        "append",
        {
            "type": "plan_update",
            "title": "Updated Plan",
            "items": [
                {"done": True, "text": "read files"},
                {"done": False, "text": "write code"},
            ],
        },
    ),
    (
        "append",
        {
            "type": "agent_event",
            "name": "explorer",
            "status": "completed",
            "elapsed": "12s",
        },
    ),
    # An ANSWERED choice_prompt mid-transcript — must render finalized (selected),
    # and must NOT be flagged live (it is not trailing).
    (
        "append",
        {
            "type": "choice_prompt",
            "question": "Pick an approach",
            "options": [
                {"number": 1, "label": "rewrite", "description": None},
                {"number": 2, "label": "patch", "description": "smaller diff"},
            ],
            "footer": None,
            "selected": 2,
            "answered": True,
            "chosen": 2,
        },
    ),
    (
        "append",
        {"type": "assistant", "phase": "final", "text": "Done.", "elapsed": "1m 02s"},
    ),
    (
        "append",
        {
            "type": "notice",
            "message": "rate limit approaching",
            "severity": "warning",
            "text": "rate limit approaching",
        },
    ),
    # An UNANSWERED choice_prompt as the LAST block — the live prompt. The Ink
    # trailing-segment heuristic must mark only this turn `isLivePrompt`.
    (
        "append",
        {
            "type": "choice_prompt",
            "question": "Continue?",
            "options": [
                {"number": 1, "label": "yes", "description": None},
                {"number": 2, "label": "no", "description": None},
            ],
            "footer": "↑/↓ to choose",
            "selected": 1,
            "answered": False,
            "chosen": None,
        },
    ),
]


def _build_wire_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Run every segment through the real producer path, capturing each emitted
    `conversation.block` wire event exactly as the bus would carry it."""
    upsert_conversation(conn, conversation_id=AGENT_ID, agent_id=AGENT_ID, harness="cc", model="m")
    events: list[dict[str, Any]] = []
    for action, seg in _SEGMENTS:
        if action == "update":
            # Mirror the live-block growth path: update_live_block mutates the
            # trailing unsealed block in place; the producer emits `block-updated`.
            updated = update_live_block(conn, AGENT_ID, seg, received_at=TS)
            assert updated, "expected a live trailing block to update"
            # Re-read the (single) live block to get its wire form.
            from murder.state.persistence.conversation import read_conversation_blocks

            block = read_conversation_blocks(conn, AGENT_ID)[-1]
            events.append(
                {
                    "type": "conversation.block",
                    "agent_id": AGENT_ID,
                    "conversation_id": AGENT_ID,
                    "action": "block-updated",
                    "block": block_to_wire(block),
                }
            )
        else:
            block = append_block(conn, AGENT_ID, seg, received_at=TS)
            events.append(
                {
                    "type": "conversation.block",
                    "agent_id": AGENT_ID,
                    "conversation_id": AGENT_ID,
                    "action": "block-appended",
                    "block": block_to_wire(block),
                }
            )
    return events


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = get_db(tmp_path / "test.db")
    init_db(db)
    return db


def test_block_to_wire_matches_golden(conn: sqlite3.Connection) -> None:
    """The real producer's wire output still equals the committed cross-language
    golden. Fails if any block wire key/type drifts on the Python side."""
    events = _build_wire_events(conn)

    if os.environ.get("REGEN_GOLDEN") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")

    assert GOLDEN_PATH.exists(), (
        f"golden missing at {GOLDEN_PATH}; regenerate with REGEN_GOLDEN=1"
    )
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert events == golden, (
        "conversation.block wire shape drifted from the committed golden. "
        "If this change is intentional, regenerate with REGEN_GOLDEN=1 and update "
        "the Ink contract test."
    )


def test_golden_covers_every_block_kind(conn: sqlite3.Connection) -> None:
    """Guard the fixture's coverage: every canonical block kind appears, incl.
    agent_event and choice_prompt (both answered and live)."""
    events = _build_wire_events(conn)
    kinds = {ev["block"]["kind"] for ev in events}
    expected = {
        "user",
        "assistant_intermediate",
        "assistant_final",
        "tool_call",
        "plan_update",
        "agent_event",
        "choice_prompt",
        "notice",
    }
    assert expected <= kinds, f"golden missing kinds: {expected - kinds}"
    # Exactly one live (trailing, unanswered) choice_prompt and one answered one.
    cps = [ev for ev in events if ev["block"]["kind"] == "choice_prompt"]
    assert len(cps) == 2
    assert cps[-1]["block"]["payload"]["answered"] is False  # live, trailing
    assert any(cp["block"]["payload"]["answered"] is True for cp in cps)
    # The `block-updated` (live-tail growth) path is exercised.
    assert any(ev["action"] == "block-updated" for ev in events)
