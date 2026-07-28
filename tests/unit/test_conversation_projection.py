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

from murder.facts.log import replay_projection_inputs
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
    project_parsed_doc(
        conn,
        agent,
        _doc(
            {"type": "user", "text": "first question (echoed from pane)"},
            _assistant("first answer"),
        ),
    )

    blocks = read_conversation_blocks(conn, agent)
    assert [(b.kind, b.payload.get("text")) for b in blocks] == [
        ("user", "first question"),
        ("assistant_final", "first answer"),
    ]

    # Turn 2: another user message, then a fuller parse covering BOTH turns.
    # n_parsed(2 non-user) < n_stored(3 incl. the interleaved user) — the old
    # count-vs-all-stored rule would discard this entirely.
    append_user_message(conn, agent, "second question")
    project_parsed_doc(
        conn,
        agent,
        _doc(
            {"type": "user", "text": "first question (echoed)"},
            _assistant("first answer"),
            {"type": "user", "text": "second question (echoed)"},
            _assistant("second answer"),
        ),
    )

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

    project_parsed_doc(
        conn, agent, _doc(_assistant("working", phase="intermediate"), state="working")
    )
    project_parsed_doc(
        conn, agent, _doc(_assistant("working on it", phase="intermediate"), state="working")
    )
    project_parsed_doc(conn, agent, _doc(_assistant("done", phase="final")))

    blocks = read_conversation_blocks(conn, agent)
    assert [(b.kind, b.payload.get("text"), b.sealed) for b in blocks] == [
        ("user", "do the thing", True),
        ("assistant_final", "done", True),
    ]
    # Exactly one trailing live block at most (here zero — final sealed).
    assert sum(1 for b in blocks if not b.sealed) == 0


def test_cumulative_projection_restores_turn_order_after_users_arrive_first(
    conn: sqlite3.Connection,
) -> None:
    """A projection catch-up must not render all users before all replies."""
    agent = "cursor-rogue-startup"
    append_user_message(conn, agent, "test")
    append_user_message(conn, agent, "test part 2")
    append_user_message(conn, agent, 'say "pingpong" please')

    project_parsed_doc(
        conn,
        agent,
        _doc(
            {"type": "user", "text": "test"},
            _assistant("Here — what do you want to work on?"),
            {"type": "user", "text": "test part 2"},
            _assistant("Still here. Ready when you are."),
            {"type": "user", "text": 'say "pingpong" please'},
            _assistant("pingpong"),
        ),
    )

    blocks = read_conversation_blocks(conn, agent)
    assert [(block.kind, block.payload.get("text")) for block in blocks] == [
        ("user", "test"),
        ("assistant_final", "Here — what do you want to work on?"),
        ("user", "test part 2"),
        ("assistant_final", "Still here. Ready when you are."),
        ("user", 'say "pingpong" please'),
        ("assistant_final", "pingpong"),
    ]


def test_unchanged_projection_repairs_and_invalidates_existing_bad_order(
    conn: sqlite3.Connection,
) -> None:
    agent = "cursor-existing-bad-order"
    doc = _doc(
        {"type": "user", "text": "one"},
        _assistant("answer one"),
        {"type": "user", "text": "two"},
        _assistant("answer two"),
    )
    append_user_message(conn, agent, "one")
    append_user_message(conn, agent, "two")
    project_parsed_doc(conn, agent, doc)
    blocks = read_conversation_blocks(conn, agent)
    by_text = {str(block.payload.get("text")): block for block in blocks}
    # Recreate the old persisted shape: all authoritative users, then replies.
    for ordinal, text in enumerate(("one", "two", "answer one", "answer two")):
        conn.execute(
            "UPDATE conversation_blocks SET ordinal = ? WHERE id = ?",
            (ordinal + 10, by_text[text].id),
        )
    conn.execute(
        "UPDATE conversation_blocks SET ordinal = ordinal - 10 WHERE conversation_id = ?",
        (agent,),
    )
    before = replay_projection_inputs(conn, projection="conversations")

    _merged, changes = project_parsed_doc_with_changes(conn, agent, doc)

    assert changes == []
    assert [block.payload.get("text") for block in read_conversation_blocks(conn, agent)] == [
        "one",
        "answer one",
        "two",
        "answer two",
    ]
    assert len(replay_projection_inputs(conn, projection="conversations")) == len(before) + 1


def test_parser_user_noise_is_not_used_as_an_order_anchor(conn: sqlite3.Connection) -> None:
    agent = "collaborator-0"
    append_user_message(conn, agent, "real question")
    project_parsed_doc(
        conn,
        agent,
        _doc(
            {"type": "user", "text": "injected system brief"},
            _assistant("real answer"),
        ),
    )

    blocks = read_conversation_blocks(conn, agent)
    assert [(block.kind, block.payload.get("text")) for block in blocks] == [
        ("user", "real question"),
        ("assistant_final", "real answer"),
    ]


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
    project_parsed_doc(
        conn,
        agent,
        _doc(
            {"type": "user", "text": brief_line},
            {"type": "user", "text": "Hit shift+tab to enable Plan Mode."},
            _assistant("real assistant reply"),
        ),
    )

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


def test_conversation_writes_emit_durable_snapshot_invalidations(
    conn: sqlite3.Connection,
) -> None:
    """The inktui refresh path tails conversations projection inputs.

    Both the authoritative user write and the later assistant projection must
    append one, so an accepted optimistic send reconciles and the reply paints
    without requiring reconnect/hydration.
    """
    agent = "cursor-rogue-startup"

    append_user_message(
        conn,
        agent,
        "test",
        client_message_id="client-test-1",
    )
    project_parsed_doc_with_changes(
        conn,
        agent,
        _doc(_assistant("Here — I'm up.")),
    )

    inputs = replay_projection_inputs(conn, projection="conversations")
    assert [(item.subject_key, item.generation) for item in inputs] == [
        (agent, 0),
        (agent, 1),
    ]


def test_duplicate_projection_does_not_emit_redundant_invalidation(
    conn: sqlite3.Connection,
) -> None:
    agent = "agent-dedup"
    doc = _doc(_assistant("still working", phase="intermediate"), state="working")

    project_parsed_doc_with_changes(conn, agent, doc)
    before = replay_projection_inputs(conn, projection="conversations")
    project_parsed_doc_with_changes(conn, agent, doc)
    after = replay_projection_inputs(conn, projection="conversations")

    assert after == before


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
    assert len(seal_updates) == 1, [(c.action, c.block.kind, c.block.sealed) for c in changes2]
    assert seal_updates[0].block.payload["text"] == "looked at the files"
    assert any(c.action == "block-appended" for c in changes2)
