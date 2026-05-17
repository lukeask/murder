"""Conversation log: pane-transcript parsing + merge/reconcile + storage."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from murder import conversation
from murder import db as dbmod
from murder import tmux as tmuxmod
from murder.agents.collaborator import CollaboratorAgent
from murder.harnesses import get as get_harness
from murder.harnesses.parsing import parse_prompt_marker_transcript
from murder.harnesses.results import ok_result

_HELPER_REPLY = (
    "Sure:\n  def add(a, b):\n      return a + b\nwrote helper.py\nDone — anything else?"
)

# A synthetic Claude-Code-shaped pane: banner, two prompt/reply turns, a tool
# box, status-bar chrome, and the trailing empty prompt.
_CC_PANE = """\
 ✓ claude@api  ·  Claude Code v2.1.3
 Welcome — type to get started.

> what's 2 + 2?
⏺ 4.

> now sketch a helper
⏺ Sure:
  def add(a, b):
      return a + b
⎿  wrote helper.py
⏺ Done — anything else?

>
  bypass permissions · ? for shortcuts
"""


def test_parse_prompt_marker_transcript_basic_shape() -> None:
    turns = parse_prompt_marker_transcript(
        _CC_PANE,
        prompt_markers=(">", "❯"),
        drop_substrings=("bypass permissions", "for shortcuts"),
    )
    assert turns == [
        ("user", "what's 2 + 2?"),
        ("assistant", "4."),
        ("user", "now sketch a helper"),
        ("assistant", _HELPER_REPLY),
    ]


def test_parse_drops_banner_and_trailing_empty_prompt() -> None:
    turns = parse_prompt_marker_transcript("hello banner\n> hi\nyo\n>\n", prompt_markers=(">",))
    assert turns == [("user", "hi"), ("assistant", "yo")]


def test_parse_no_markers_or_no_prompt_returns_empty() -> None:
    assert parse_prompt_marker_transcript(_CC_PANE, prompt_markers=()) == []
    assert parse_prompt_marker_transcript("text\nno prompts here", prompt_markers=(">",)) == []


def test_parse_drops_slash_command_prompts_with_args() -> None:
    pane = "❯ /model opus\n  ⎿  Kept model as Opus\n❯ real ask\n● real answer\n❯\n"
    turns = parse_prompt_marker_transcript(
        pane,
        prompt_markers=("❯",),
    )
    assert turns == [("user", "real ask"), ("assistant", "real answer")]


def test_adapter_parse_transcript_uses_configured_markers() -> None:
    turns = get_harness("claude_code").parse_transcript(_CC_PANE)
    assert turns[0] == ("user", "what's 2 + 2?")
    assert turns[1] == ("assistant", "4.")
    # status-bar line did not leak into the last assistant turn
    assert all("bypass permissions" not in t for _, t in turns)


# ── merge / reconcile ──────────────────────────────────────────────────────


def _turns(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    return list(pairs)


def test_merge_first_parse_persists_and_returns(memdb) -> None:
    parsed = _turns(("user", "hi"), ("assistant", "hello"))
    assert conversation.merge_transcript(memdb, "collaborator-0", parsed) == parsed
    assert conversation.read_conversation(memdb, "collaborator-0") == parsed
    rows = dbmod.get_agent_messages(memdb, "collaborator-0")
    assert [r["ordinal"] for r in rows] == [0, 1]


def test_merge_empty_parse_keeps_stored(memdb) -> None:
    stored = _turns(("user", "hi"), ("assistant", "hello"))
    conversation.merge_transcript(memdb, "a", stored)
    assert conversation.merge_transcript(memdb, "a", []) == stored
    assert conversation.read_conversation(memdb, "a") == stored


def test_merge_longer_parse_replaces(memdb) -> None:
    conversation.merge_transcript(memdb, "a", _turns(("user", "hi"), ("assistant", "hello")))
    longer = _turns(("user", "hi"), ("assistant", "hello"), ("user", "bye"), ("assistant", "ciao"))
    assert conversation.merge_transcript(memdb, "a", longer) == longer
    assert conversation.read_conversation(memdb, "a") == longer


def test_merge_same_length_growing_last_turn_replaces(memdb) -> None:
    conversation.merge_transcript(memdb, "a", _turns(("user", "hi"), ("assistant", "hel")))
    grown = _turns(("user", "hi"), ("assistant", "hello there"))
    assert conversation.merge_transcript(memdb, "a", grown) == grown
    assert conversation.read_conversation(memdb, "a") == grown


def test_merge_shorter_parse_is_treated_as_transient_noise(memdb) -> None:
    full = _turns(("user", "hi"), ("assistant", "hello"), ("user", "bye"))
    conversation.merge_transcript(memdb, "a", full)
    assert conversation.merge_transcript(memdb, "a", _turns(("user", "bye"))) == full
    assert conversation.read_conversation(memdb, "a") == full


# ── CollaboratorAgent.refresh_transcript ───────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_transcript_captures_parses_and_merges(monkeypatch, memdb, tmp_path) -> None:
    async def fake_capture(session, lines=200):  # noqa: ARG001
        return _CC_PANE

    monkeypatch.setattr("murder.agents.collaborator.tmux.capture_pane", fake_capture)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collab",
        harness=get_harness("claude_code"),
        repo_root=tmp_path,
        runtime=SimpleNamespace(db=memdb, bus=None, run_id=None, sync_agent=lambda a: None),  # type: ignore[arg-type]
    )
    turns = await agent.refresh_transcript()
    assert turns[0] == ("user", "what's 2 + 2?")
    assert conversation.read_conversation(memdb, "collaborator-0") == turns


@pytest.mark.asyncio
async def test_start_clears_prior_conversation_log(monkeypatch, memdb, tmp_path) -> None:
    # A previous run left a transcript under the same agent id.
    conversation.merge_transcript(memdb, "collaborator-0", _turns(("user", "old")))

    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collab",
        harness=get_harness("claude_code"),
        repo_root=tmp_path,
        runtime=SimpleNamespace(db=memdb, bus=None, run_id=None, sync_agent=lambda a: None),  # type: ignore[arg-type]
    )

    async def ok(*a, **k):  # noqa: ANN002, ANN003, ARG001
        return ok_result()

    async def yes(*a, **k):  # noqa: ANN002, ANN003, ARG001
        return True

    monkeypatch.setattr(agent.harness_session, "start", ok)
    monkeypatch.setattr(agent.harness_session, "send_prompt", ok)
    monkeypatch.setattr("murder.agents.collaborator.tmux.session_exists", yes)

    await agent.start("hello", {})
    assert conversation.read_conversation(memdb, "collaborator-0") == []


@pytest.mark.asyncio
async def test_refresh_transcript_session_gone_returns_empty(monkeypatch, tmp_path) -> None:
    async def boom(session, lines=200):  # noqa: ARG001
        raise tmuxmod.TmuxError("no session")

    monkeypatch.setattr("murder.agents.collaborator.tmux.capture_pane", boom)
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="gone",
        harness=get_harness("claude_code"),
        repo_root=tmp_path,
    )
    assert await agent.refresh_transcript() == []
