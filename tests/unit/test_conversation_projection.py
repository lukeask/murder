"""Phase 1.c — server-side projection with ground-truth user blocks.

Covers the projector that unifies collaborator/crow/planner parsing:

- ``merge_non_user_segments`` reconciles a parsed (user-stripped) doc against
  the *non-user projection* of the stored, interleaved stream — so a parse is
  applied, not dropped as "shorter than storage", once ground-truth user blocks
  are interleaved (the reconcile blocker this phase fixes).
- ``project_parsed_doc`` strips re-derived user segments so the collaborator
  corruption (injected brief mislabelled as turns) cannot recur.
- A real harness fixture projects cleanly into the store.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from murder.state.persistence.agents import get_agent_messages
from murder.state.persistence.conversation import (
    append_user_message,
    merge_non_user_segments,
    project_parsed_doc,
    project_parsed_doc_with_changes,
    read_conversation_blocks,
    read_conversation_doc,
)
from murder.state.persistence.schema import get_db, init_db

_CC_EXPECTED = Path(__file__).parent.parent / "fixtures" / "transcripts" / "cc" / "expected.json"
_CODEX_EXPECTED = (
    Path(__file__).parent.parent / "fixtures" / "transcripts" / "codex" / "expected.json"
)


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    db = get_db(tmp_path / "test.db")
    init_db(db)
    return db


def _assistant(text: str, phase: str = "final") -> dict[str, object]:
    return {"type": "assistant", "phase": phase, "text": text, "elapsed": None}


def _doc(*segments: dict[str, object], state: str = "awaiting_input") -> dict[str, object]:
    return {"harness": "claude_code", "state": state, "condensed": None, "segments": list(segments)}


# ---------------------------------------------------------------------------
# The reconcile blocker: parse applied against an interleaved ground-truth stream
# ---------------------------------------------------------------------------

def test_project_applies_parse_against_interleaved_ground_truth(conn: sqlite3.Connection) -> None:
    """Ground-truth user blocks are interleaved into storage, but the parsed
    doc has its user segments stripped. Reconciling the stripped parse against
    *all* stored blocks would look shorter and get dropped as pane noise — the
    bug this phase fixes. Assert the parse is applied and users survive.
    """
    agent = "agent-1"

    # Turn 1: user sends, assistant replies. The parsed pane re-derives the user
    # echo (stripped) plus the assistant turn.
    append_user_message(conn, agent, "first question")
    project_parsed_doc(conn, agent, _doc(
        {"type": "user", "text": "first question (echoed from pane)"},
        _assistant("first answer"),
    ))

    blocks = read_conversation_blocks(conn, agent)
    assert [(b.kind, b.payload.get("text")) for b in blocks] == [
        ("user", "first question"),
        ("assistant_final", "first answer"),
    ]

    # Turn 2: another user message, then a fuller parse covering BOTH turns.
    # n_parsed(2 non-user) < n_stored(3 incl. the interleaved user) — the old
    # count-vs-all-stored rule would discard this entirely.
    append_user_message(conn, agent, "second question")
    project_parsed_doc(conn, agent, _doc(
        {"type": "user", "text": "first question (echoed)"},
        _assistant("first answer"),
        {"type": "user", "text": "second question (echoed)"},
        _assistant("second answer"),
    ))

    doc = read_conversation_doc(conn, agent)
    assert doc is not None
    rendered = [(s.get("type"), s.get("phase"), s.get("text")) for s in doc["segments"]]
    assert rendered == [
        ("user", None, "first question"),
        ("assistant", "final", "first answer"),
        ("user", None, "second question"),
        ("assistant", "final", "second answer"),
    ]

    # Flat compat log: ground-truth users + parsed assistants, no echoes.
    flat = [(m["role"], m["body"]) for m in get_agent_messages(conn, agent)]
    assert flat == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]


def test_project_grows_live_assistant_tail_after_user(conn: sqlite3.Connection) -> None:
    """An in-progress assistant turn after a ground-truth user block updates the
    live trailing block in place rather than appending duplicates."""
    agent = "agent-1"
    append_user_message(conn, agent, "do the thing")

    project_parsed_doc(conn, agent, _doc(_assistant("working", phase="intermediate"),
                                         state="working"))
    project_parsed_doc(conn, agent, _doc(_assistant("working on it", phase="intermediate"),
                                         state="working"))
    project_parsed_doc(conn, agent, _doc(_assistant("done", phase="final")))

    blocks = read_conversation_blocks(conn, agent)
    assert [(b.kind, b.payload.get("text"), b.sealed) for b in blocks] == [
        ("user", "do the thing", True),
        ("assistant_final", "done", True),
    ]
    # Exactly one trailing live block at most (here zero — final sealed).
    assert sum(1 for b in blocks if not b.sealed) == 0


def test_project_grows_sealed_assistant_final_prefix(conn: sqlite3.Connection) -> None:
    """Cursor can briefly look idle while a final reply is still only a prefix.

    The next parse at the same position must be able to replace that sealed
    prefix with the longer same-kind final block.
    """
    agent = "agent-1"
    append_user_message(conn, agent, "test")

    project_parsed_doc(conn, agent, _doc(_assistant("Hear")))
    _merged, changes = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("Hearing you loud and clear.")),
    )

    blocks = read_conversation_blocks(conn, agent)
    assert [(b.kind, b.payload.get("text"), b.sealed) for b in blocks] == [
        ("user", "test", True),
        ("assistant_final", "Hearing you loud and clear.", True),
    ]
    assert [c.action for c in changes] == ["block-updated"]
    assert changes[0].block.payload["text"] == "Hearing you loud and clear."


# ---------------------------------------------------------------------------
# Corruption regression: re-derived user segments never become turns
# ---------------------------------------------------------------------------

def test_project_strips_re_derived_user_segments(conn: sqlite3.Connection) -> None:
    """The collaborator corruption was murder's injected brief re-derived from
    the pane as alternating user/assistant turns. The projector strips *all*
    parsed user segments; only ground-truth users (recorded at send) survive.
    """
    agent = "collaborator-0"
    brief_line = "You are the user's general-purpose helper inside the murder TUI."

    append_user_message(conn, agent, "real user question")
    project_parsed_doc(conn, agent, _doc(
        {"type": "user", "text": brief_line},
        {"type": "user", "text": "Hit shift+tab to enable Plan Mode."},
        _assistant("real assistant reply"),
    ))

    doc = read_conversation_doc(conn, agent)
    assert doc is not None
    user_texts = [s["text"] for s in doc["segments"] if s.get("type") == "user"]
    assert user_texts == ["real user question"]
    assert brief_line not in json.dumps(doc)


# ---------------------------------------------------------------------------
# Real fixture projects cleanly
# ---------------------------------------------------------------------------

def test_cc_fixture_projects_non_user_segments(conn: sqlite3.Connection) -> None:
    """A real Claude Code transcript doc projects all its non-user segments into
    the store in order; its (re-derived) user segments are stripped."""
    agent = "crow-t001"
    doc = json.loads(_CC_EXPECTED.read_text())
    expected_non_user = [s for s in doc["segments"] if s.get("type") != "user"]

    project_parsed_doc(conn, agent, doc)

    blocks = read_conversation_blocks(conn, agent)
    assert [b.payload for b in blocks] == expected_non_user
    assert all(b.kind != "user" for b in blocks)


def test_codex_fixture_projects_non_user_segments(conn: sqlite3.Connection) -> None:
    """A real Codex transcript doc projects all its non-user segments into
    the store in order, using the same unified path as Claude Code."""
    agent = "crow-codex-t001"
    doc = json.loads(_CODEX_EXPECTED.read_text())
    expected_non_user = [s for s in doc["segments"] if s.get("type") != "user"]

    project_parsed_doc(conn, agent, doc)

    blocks = read_conversation_blocks(conn, agent)
    assert [b.payload for b in blocks] == expected_non_user
    assert all(b.kind != "user" for b in blocks)


def test_merge_non_user_segments_ignores_shorter_parse(conn: sqlite3.Connection) -> None:
    """A parse with fewer non-user segments than stored is transient pane noise
    and must not truncate the stored conversation."""
    agent = "agent-1"
    append_user_message(conn, agent, "q")
    project_parsed_doc(conn, agent, _doc(_assistant("a1"), _assistant("a2")))

    before = read_conversation_blocks(conn, agent)
    # Reconcile a single non-user segment against two stored — ignored.
    merge_non_user_segments(conn, agent, [_assistant("a1")])
    after = read_conversation_blocks(conn, agent)

    assert [b.payload for b in after] == [b.payload for b in before]


def test_project_reports_changes_only_for_real_mutations(conn: sqlite3.Connection) -> None:
    """1.d push dedupe boundary: first parse appends, duplicate parse emits no
    changes, and a growing live tail reports one update."""
    agent = "agent-1"

    _doc1, changes1 = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("working", phase="intermediate"), state="working"),
    )
    _doc2, changes2 = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("working", phase="intermediate"), state="working"),
    )
    _doc3, changes3 = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("working more", phase="intermediate"), state="working"),
    )

    assert [c.action for c in changes1] == ["block-appended"]
    assert changes2 == []
    assert [c.action for c in changes3] == ["block-updated"]
    assert changes3[0].block.payload["text"] == "working more"


def test_superseding_a_live_intermediate_emits_its_seal_as_update(
    conn: sqlite3.Connection,
) -> None:
    """Appending past a live ``assistant_intermediate`` emits a seal ``block-updated``.

    Regression (Condensed-view break): a streaming intermediate assistant block is
    written ``sealed=0`` and seals silently (an in-place UPDATE) the moment a later
    segment supersedes it — that seal carried NO change. Downstream consumers that
    key off ``block.sealed`` (the producer's condensed summarization buffer only
    buffers SEALED intermediate blocks) therefore never saw the block as sealed and
    skipped it forever, so its prose was never summarized and Condensed rendered it
    verbatim. The reconcile must now emit a ``block-updated`` for the now-sealed
    predecessor so the seal is observable.
    """
    agent = "agent-seal"

    # 1) A live intermediate assistant block (stays unsealed while streaming).
    _d1, changes1 = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("looked at the files", phase="intermediate"), state="working"),
    )
    assert [c.action for c in changes1] == ["block-appended"]
    assert changes1[0].block.sealed is False

    # 2) A second segment appears AFTER it → the first block must seal.
    _d2, changes2 = project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(
            _assistant("looked at the files", phase="intermediate"),
            _assistant("done", phase="final"),
            state="awaiting_input",
        ),
    )

    # The now-sealed predecessor surfaces as a block-updated with sealed=True,
    # ordered before the newly-appended final block.
    seal_updates = [
        c
        for c in changes2
        if c.action == "block-updated"
        and c.block.kind == "assistant_intermediate"
        and c.block.sealed is True
    ]
    assert len(seal_updates) == 1, [
        (c.action, c.block.kind, c.block.sealed) for c in changes2
    ]
    assert seal_updates[0].block.payload["text"] == "looked at the files"
    assert any(c.action == "block-appended" for c in changes2)
