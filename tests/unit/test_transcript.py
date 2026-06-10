"""Ground-truth tests for the harness transcript parsing stack.

Assumed API:

    import murder.llm.harnesses.transcripts as transcripts

    # stateful, appending: feed pane captures in order, read the accumulated doc
    acc = transcripts.TranscriptAccumulator(harness="claude_code")
    for frame in frames:
        acc.feed(frame)
    doc = acc.to_dict()        # -> TranscriptDoc dict matching SCHEMA.md

    # convenience: feed a whole sequence at once
    doc = transcripts.parse_frames("claude_code", frames)

``doc`` is the discriminated-union document defined in SCHEMA.md:
``{"harness","state","condensed","segments":[...]}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import murder.llm.harnesses.transcripts as transcripts

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
_HARNESSES = ["cc", "codex", "cursor", "pi", "antigravity"]
_HARNESS_KIND = {
    "cc": "claude_code",
    "codex": "codex",
    "cursor": "cursor",
    "pi": "pi",
    "antigravity": "antigravity",
}

# Chrome / live-UI strings that must NEVER survive into a parsed segment.
_CHROME_NEVER = [
    "bypass permissions",
    "esc to interrupt",
    "ctrl+o to expand",
    "ctrl+t to view transcript",
    "Tip:",
    "tokens",
    "shift+tab to cycle",
    "uncached",
    "/clear to start fresh",
    "Find and fix a bug in @filename",
    "? for shortcuts",
    "Antigravity CLI",
]


def _frames(harness: str) -> list[str]:
    fdir = _FIXTURES / harness / "frames"
    return [p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.txt"))]


def _expected(harness: str) -> dict:
    return json.loads((_FIXTURES / harness / "expected.json").read_text(encoding="utf-8"))


def _strip_frame_header(text: str) -> str:
    """Strip fixture metadata comment lines (# source: ..., # terminal width ...)
    that the recording tool prepends — these are not pane content."""
    lines = text.split("\n")
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    return "\n".join(lines)


def _scenario_frames(name: str) -> list[str]:
    fdir = _FIXTURES / name / "frames"
    return [_strip_frame_header(p.read_text(encoding="utf-8")) for p in sorted(fdir.glob("*.txt"))]


def _scenario_expected(name: str) -> dict:
    return json.loads((_FIXTURES / name / "expected.json").read_text(encoding="utf-8"))


# Ground-truth user turns per fixture, supplied to the parser the way the
# producer does in production (recorded authoritatively at the send boundary).
# Markerless cursor uses these as anchors to label echoed user content; without
# them (and without colour escapes in the plain fixtures) it treats every block
# as assistant.
_FIXTURE_USER_TEXTS: dict[str, list[str]] = {
    "cursor": ["test", "test2"],
}


def _parse(harness: str) -> dict:
    return transcripts.parse_frames(
        _HARNESS_KIND[harness],
        _frames(harness),
        user_texts=_FIXTURE_USER_TEXTS.get(harness),
    )


def _segs(doc: dict) -> list[dict]:
    return doc["segments"]


# --------------------------------------------------------------------------- #
# Whole-document ground truth (the strongest assertion; the granular tests
# below exist to localize failures).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", _HARNESSES)
def test_full_doc_matches_expected(harness):
    assert _parse(harness) == _expected(harness)


@pytest.mark.parametrize("harness", _HARNESSES)
def test_segment_type_sequence_matches(harness):
    got = [s["type"] for s in _segs(_parse(harness))]
    want = [s["type"] for s in _expected(harness)["segments"]]
    assert got == want


# --------------------------------------------------------------------------- #
# State detection (read from chrome, not transcript).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", _HARNESSES)
def test_ends_awaiting_input(harness):
    # both captured sessions end idle at an empty/placeholder input box
    assert _parse(harness)["state"] == "awaiting_input"


@pytest.mark.parametrize("harness", _HARNESSES)
def test_condensed_is_null_for_deterministic_parse(harness):
    # condensed is a separate small-LLM pass; the deterministic parser leaves it null
    assert _parse(harness)["condensed"] is None


# --------------------------------------------------------------------------- #
# Chrome / live-input suppression.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", _HARNESSES)
def test_no_chrome_leaks_into_any_segment(harness):
    blob = json.dumps(_parse(harness), ensure_ascii=False)
    for needle in _CHROME_NEVER:
        assert needle not in blob, f"chrome leaked into parse: {needle!r}"


def test_cc_uncached_notice_suppressed_and_idle():
    """CC status-bar uncached-tokens notice (recording 20260604-214229) is chrome only."""
    fdir = _FIXTURES / "cc_uncached" / "frames"
    frames = [p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.txt"))]
    assert any("uncached" in frame for frame in frames), "fixture must include uncached notice"
    assert any("/clear to start fresh" in frame for frame in frames), "fixture must include idle chrome"

    acc = transcripts.TranscriptAccumulator("claude_code")
    for frame in frames:
        acc.feed(frame)
        doc = acc.to_dict()
        blob = json.dumps(doc, ensure_ascii=False)
        assert "uncached" not in blob
        assert "/clear to start fresh" not in blob
        assert doc["state"] == "awaiting_input"

    assert acc.to_dict() == transcripts.parse_frames("claude_code", frames)


def test_cc_unsent_live_input_is_not_a_turn():
    """The final CC frame shows ``❯ yeah, sketch the diff3…`` then ``❯ d`` being
    typed — both are live input that was never submitted, so neither may appear
    as a user segment."""
    doc = _parse("cc")
    users = [s["text"] for s in _segs(doc) if s["type"] == "user"]
    assert "yeah, sketch the diff3 reconcile path against sync.py" not in users
    assert "d" not in users
    assert not any(u.strip() in {"d", "yeah, sketch the diff3 reconcile path against sync.py"} for u in users)


# --------------------------------------------------------------------------- #
# User turns: de-wrapped, deduped across frames (no scroll-off loss/dupes).
# --------------------------------------------------------------------------- #
def test_cc_has_three_user_turns_dewrapped():
    users = [s["text"] for s in _segs(_parse("cc")) if s["type"] == "user"]
    assert len(users) == 3
    assert users[0].startswith("dont need fixes, but currently the sync status")
    # de-wrapped: the wrapped continuation is joined, no embedded newlines
    assert "\n" not in users[0]
    assert users[0].endswith("what the deal is?")


def test_codex_has_single_user_turn():
    users = [s["text"] for s in _segs(_parse("codex")) if s["type"] == "user"]
    assert users == [
        "please read db-sync-handoff.md in .murder/reports and implement the plan described in the handoff"
    ]


# --------------------------------------------------------------------------- #
# intermediate vs final phase boundary.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", _HARNESSES)
def test_final_blocks_carry_elapsed(harness):
    finals = [s for s in _segs(_parse(harness)) if s["type"] == "assistant" and s["phase"] == "final"]
    assert finals, "expected at least one final assistant block"
    # The terminal final of a turn carries the completion-marker duration *when
    # the pane rendered one*. CC shows `✻ Worked/Baked for …` markers; the codex
    # capture has NO `─ Worked for … ─` marker anywhere (verified by grep), so
    # its single final closes at idle with elapsed=None — see
    # test_codex_one_final_at_end. The earlier fixture hardcoded "13m 06s" for
    # codex; that value is not derivable from the frames.
    if harness == "cc":
        assert any(f.get("elapsed") for f in finals)


@pytest.mark.parametrize("harness", _HARNESSES)
def test_intermediate_phase_never_has_elapsed(harness):
    for s in _segs(_parse(harness)):
        if s["type"] == "assistant" and s["phase"] == "intermediate":
            assert s.get("elapsed") is None


def test_codex_one_final_at_end():
    segs = _segs(_parse("codex"))
    finals = [i for i, s in enumerate(segs) if s["type"] == "assistant" and s["phase"] == "final"]
    assert len(finals) == 1
    # The codex capture contains no `─ Worked for … ─` completion marker (verified
    # by grep over codex/frames/*.txt), so elapsed is None. The turn is closed
    # structurally by the idle input placeholder at the bottom of the pane. The
    # prior fixture's "13m 06s" was fabricated and is not present in any frame.
    assert segs[finals[0]]["elapsed"] is None
    # It is the last assistant block of the transcript.
    assert finals[0] == max(
        i for i, s in enumerate(segs) if s["type"] == "assistant"
    )


# --------------------------------------------------------------------------- #
# tool_call fidelity.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("harness", _HARNESSES)
def test_tool_call_field_types(harness):
    # result and elided are INDEPENDENT: a tool may show the first lines then
    # collapse the rest (result set + elided true), or be fully collapsed
    # (result null + elided true). Only the field shapes are invariant.
    for s in _segs(_parse(harness)):
        if s["type"] == "tool_call":
            assert isinstance(s["elided"], bool)
            assert s.get("result") is None or isinstance(s["result"], str)
            assert s.get("input") is None or isinstance(s["input"], str)


@pytest.mark.parametrize("harness", _HARNESSES)
def test_tool_titles_have_no_expand_hints(harness):
    for s in _segs(_parse(harness)):
        if s["type"] == "tool_call":
            assert "ctrl+o" not in s["title"]
            assert "ctrl+t" not in s["title"]


# --------------------------------------------------------------------------- #
# Structural segments unique to each harness.
# --------------------------------------------------------------------------- #
def test_cc_emits_agent_events():
    events = [s for s in _segs(_parse("cc")) if s["type"] == "agent_event"]
    statuses = [e["status"] for e in events]
    assert "dispatched" in statuses and "completed" in statuses
    completed = next(e for e in events if e["status"] == "completed")
    assert completed["name"] == "Reconcile conflicted plan"
    assert completed["elapsed"] == "2m 40s"


def test_codex_emits_two_plan_updates_last_all_done():
    plans = [s for s in _segs(_parse("codex")) if s["type"] == "plan_update"]
    assert len(plans) == 2
    first, last = plans
    assert sum(1 for it in first["items"] if it["done"]) == 1
    assert all(it["done"] for it in last["items"])
    assert len(last["items"]) == 6


# --------------------------------------------------------------------------- #
# Appending / statefulness — the core reason the parser is frame-sequence based.
# --------------------------------------------------------------------------- #
def _accumulator(harness: str):
    acc = transcripts.TranscriptAccumulator(_HARNESS_KIND[harness])
    user_texts = _FIXTURE_USER_TEXTS.get(harness)
    if user_texts is not None:
        acc.user_texts = user_texts
    return acc


@pytest.mark.parametrize("harness", _HARNESSES)
def test_segment_count_is_monotonic_while_feeding(harness):
    acc = _accumulator(harness)
    counts = []
    for frame in _frames(harness):
        acc.feed(frame)
        counts.append(len(acc.to_dict()["segments"]))
    assert counts == sorted(counts), "committed segment count must never shrink frame-to-frame"


@pytest.mark.parametrize("harness", _HARNESSES)
def test_refeeding_last_frame_is_idempotent(harness):
    acc = _accumulator(harness)
    frames = _frames(harness)
    for frame in frames:
        acc.feed(frame)
    before = acc.to_dict()
    acc.feed(frames[-1])  # re-showing the same pane must not duplicate anything
    assert acc.to_dict() == before


@pytest.mark.parametrize("harness", _HARNESSES)
def test_incremental_feed_equals_batch_parse(harness):
    acc = _accumulator(harness)
    for frame in _frames(harness):
        acc.feed(frame)
    assert acc.to_dict() == _parse(harness)


def test_cc_choice_prompt_unanswered_fixture_matches_expected():
    doc = transcripts.parse_frames("claude_code", _scenario_frames("cc_mc_awaiting_approval"))
    assert doc == _scenario_expected("cc_mc_awaiting_approval")


def test_cc_choice_prompt_answered_fixture_matches_expected():
    doc = transcripts.parse_frames("claude_code", _scenario_frames("cc_mc_answered"))
    assert doc == _scenario_expected("cc_mc_answered")


def test_cc_choice_prompt_cursor_motion_updates_in_place():
    acc = transcripts.TranscriptAccumulator("claude_code")
    frames = _scenario_frames("cc_mc_answered")
    acc.feed(frames[0])
    first = acc.to_dict()
    acc.feed(frames[1])
    second = acc.to_dict()
    assert len(first["segments"]) == len(second["segments"]) == 2
    assert [segment["type"] for segment in second["segments"]].count("choice_prompt") == 1
    assert second["segments"][-1]["answered"] is False
    assert first["segments"][-1]["selected"] == 1
    assert second["segments"][-1]["selected"] == 6


def test_cc_choice_prompt_resolution_marks_answered_with_selected_option():
    acc = transcripts.TranscriptAccumulator("claude_code")
    for frame in _scenario_frames("cc_mc_answered"):
        acc.feed(frame)
    doc = acc.to_dict()
    choice = next(segment for segment in doc["segments"] if segment["type"] == "choice_prompt")
    assert doc["state"] == "awaiting_input"
    assert choice["answered"] is True
    assert choice["chosen"] == 6


# --------------------------------------------------------------------------- #
# Cursor-specific: two user/assistant turns, no tool calls, elapsed always null.
# --------------------------------------------------------------------------- #
def test_cursor_has_two_user_turns():
    users = [s["text"] for s in _segs(_parse("cursor")) if s["type"] == "user"]
    assert users == ["test", "test2"]


def test_cursor_has_two_final_assistant_blocks():
    segs = _segs(_parse("cursor"))
    finals = [s for s in segs if s["type"] == "assistant" and s["phase"] == "final"]
    assert len(finals) == 2
    # cursor shows no completion-marker duration; elapsed is always null
    assert all(f["elapsed"] is None for f in finals)


def test_cursor_has_no_tool_calls():
    segs = _segs(_parse("cursor"))
    assert not any(s["type"] == "tool_call" for s in segs)


def test_cursor_no_chrome_in_segments():
    cursor_chrome = [
        "Cursor Agent",
        "Use /mcp",
        "Add a follow-up",
        "Plan, search, build anything",
        "Composer",
        "Auto-run",
        "ctrl+c to stop",
    ]
    blob = json.dumps(_parse("cursor"), ensure_ascii=False)
    for needle in cursor_chrome:
        assert needle not in blob, f"cursor chrome leaked into parse: {needle!r}"


# --------------------------------------------------------------------------- #
# Pi-specific: one user/assistant turn, reasoning prefix stripped.
# --------------------------------------------------------------------------- #
def test_pi_has_one_user_turn():
    users = [s["text"] for s in _segs(_parse("pi")) if s["type"] == "user"]
    assert users == ["say hello in one word"]


def test_pi_has_one_final_assistant_block():
    segs = _segs(_parse("pi"))
    finals = [s for s in segs if s["type"] == "assistant" and s["phase"] == "final"]
    assert len(finals) == 1
    assert finals[0]["text"] == "Hello"


def test_pi_no_tool_calls():
    segs = _segs(_parse("pi"))
    assert not any(s["type"] == "tool_call" for s in segs)


def test_pi_no_chrome_in_segments():
    pi_chrome = [
        "The user wants",
        "ctrl+o to expand",
        "compacted from",
        "Update Available",
        "pi update",
        "[compaction]",
    ]
    blob = json.dumps(_parse("pi"), ensure_ascii=False)
    for needle in pi_chrome:
        assert needle.lower() not in blob.lower(), f"pi chrome leaked: {needle!r}"


# --------------------------------------------------------------------------- #
# Antigravity-specific: two user turns, one assistant, second turn interrupted.
# --------------------------------------------------------------------------- #
def test_antigravity_has_two_user_turns():
    users = [s["text"] for s in _segs(_parse("antigravity")) if s["type"] == "user"]
    assert users == ["Reply with exactly: OK", "Reply with exactly: OK"]


def test_antigravity_has_one_final_assistant_block():
    segs = _segs(_parse("antigravity"))
    finals = [s for s in segs if s["type"] == "assistant" and s["phase"] == "final"]
    assert len(finals) == 1
    assert finals[0]["text"] == "Prioritizing Tool Usage OK"


def test_antigravity_no_tool_calls():
    segs = _segs(_parse("antigravity"))
    assert not any(s["type"] == "tool_call" for s in segs)


def test_antigravity_no_chrome_in_segments():
    agy_chrome = [
        "? for shortcuts",
        "esc to cancel",
        "Generating...",
        "Antigravity CLI",
        "Interrupted",
        "user@example.com",
        "▸ Thought for",
    ]
    blob = json.dumps(_parse("antigravity"), ensure_ascii=False)
    for needle in agy_chrome:
        assert needle not in blob, f"agy chrome leaked: {needle!r}"


# --------------------------------------------------------------------------- #
# Injected system prompt: murder sends its crow system prompt as the session's
# first user message. Markerless harnesses (cursor, pi) echo it as a user turn
# they never answer; left in place it inverts every later role. Because murder
# owns the exact text, the parser drops the matching leading blocks.
# --------------------------------------------------------------------------- #
_COLLAB_SYSTEM_PROMPT = (
    "You are the user's general-purpose helper inside the murder TUI. Your cwd "
    "is the repo root. You run as a long-lived session and auto-restart on "
    "death. Murder is an agent orchestration metaharness. Your role in the "
    "system is to generally assist the user however they ask.\n\n"
    "Murder keeps state for you in the .murder subdirectory of the project. If "
    "a user mentions a note, it is likely in .murder/notes and plans live in "
    ".murder/plans. Only read these if directly relevant to the conversation.\n\n"
    "Plan `.md` files in `.murder/plans` must start with YAML frontmatter; "
    "ticket `.md` files must not. Ticket YAML is only metadata/carving output "
    "when requested."
)


def _cursor_frame_with_system_prompt() -> str:
    """A cursor pane where the system prompt was echoed as the first user turn."""
    body_paragraphs = "\n\n".join(
        f"  {para}" for para in _COLLAB_SYSTEM_PROMPT.split("\n\n")
    )
    return (
        "user@machine:~/Documents/code/murder $ agent\n"
        "\n\n"
        "  Cursor Agent\n"
        "  v2026.06.04-8f81907\n"
        "  Use /mcp to connect Cursor to your tools and data sources.\n"
        "\n\n"
        f"{body_paragraphs}\n"
        "\n\n"
        "  test\n"
        "\n\n"
        "  Here — what do you want to work on?\n"
        "\n"
        " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
        "  → Add a follow-up\n"
        " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "  Composer 2.5 · 7.3%                                    Auto-run\n"
        "  ~/Documents/code/murder · fix/shutdown-flock-race\n"
    )


def test_cc_multiline_brief_captured_as_single_user_turn():
    """A multi-paragraph collaborator brief sent as ``❯ <text>`` must be parsed
    as a single user segment, not split into user + phantom assistant turns.

    Regression guard: blank lines between paragraphs of the brief used to stop
    user-turn consumption, causing subsequent indented paragraphs to fall through
    to the catch-all and appear as assistant intermediate blocks — which would then
    survive the project_parsed_doc_with_changes user-strip and show in the chat.
    """
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    pane_lines = [
        "❯ You are a collaborator helping plan the feature.",
        "",
        "  Please analyze the codebase carefully.",
        "",
        "  Focus on the architecture and interfaces.",
        "",
        "● Starting the session",
        "  I'll help you plan this.",
    ]
    segs = cc.parse_lines(pane_lines)

    user_segs = [s for s in segs if s["type"] == "user"]
    assistant_segs = [s for s in segs if s["type"] == "assistant"]

    assert len(user_segs) == 1, f"expected 1 user segment, got {len(user_segs)}: {user_segs}"
    assert "collaborator" in user_segs[0]["text"]
    # None of the brief paragraphs should have leaked into assistant segments.
    for asst in assistant_segs:
        assert "analyze the codebase" not in asst.get("text", "")
        assert "architecture and interfaces" not in asst.get("text", "")


def test_cc_slash_command_echo_is_not_a_user_turn():
    """A ``❯ /model opus`` prompt echo is CC chrome (the harness echoing a slash
    command), not a user turn — it must not produce a user segment. A real typed
    question still does. Regression: the parse_spanned user branch emitted any
    non-empty non-live ``❯`` line, resurrecting fake slash-command turns the old
    parsing.py suppressed via _SLASH_COMMAND_RE."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    slash = cc.parse_lines(["❯ /model opus", "● ok"])
    assert [s for s in slash if s["type"] == "user"] == []

    clear = cc.parse_lines(["❯ /clear", "● ok"])
    assert [s for s in clear if s["type"] == "user"] == []

    real = cc.parse_lines(["❯ what does this function do?", "● ok"])
    real_users = [s for s in real if s["type"] == "user"]
    assert len(real_users) == 1
    assert "what does this function" in real_users[0]["text"]


def _cursor_paint_user_blocks(frame: str, user_texts: list[str]) -> str:
    """Re-create Cursor's user-input background colour around the given lines.

    Cursor paints submitted user blocks with SGR bg ``48;2;36;36;40``; the
    plain fixtures dropped it. This wraps the matching content lines so a frame
    exercises the colour-marker path the way a real ``-e`` capture would.
    """
    needles = {t.strip() for t in user_texts}
    out: list[str] = []
    for line in frame.splitlines():
        if line.strip() in needles:
            out.append(f"\x1b[48;2;36;36;40m{line}\x1b[49m")
        else:
            out.append(line)
    return "\n".join(out)


def test_cursor_input_box_continuation_is_chrome():
    """The live composer can hold wrapped text whose continuation lines carry no
    ``→`` marker — only the input-box background colour. Those lines must be
    suppressed as chrome, not leak into the transcript as an assistant turn.

    Regression: a brief left sitting in the input box surfaced its wrapped tail
    ("...the system is to generally assist...") as a ``collaborator:`` message.
    """
    input_bg = "\x1b[48;2;21;21;21m"
    frame = (
        "  Understood. I'm your helper.\n"
        "\n"
        " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
        f"{input_bg} → You are the user's general-purpose helper. Your role in the\x1b[49m\n"
        f"{input_bg}   system is to generally assist the user however they ask.\x1b[49m\n"
        " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "  Composer 2.5 · 7.3%                                    Auto-run\n"
        "  ~/Documents/code/murder · fix/shutdown-flock-race\n"
    )
    doc = transcripts.parse_frames("cursor", [frame])
    blob = json.dumps(doc, ensure_ascii=False)
    assert "generally assist the user" not in blob
    assert [s["text"] for s in doc["segments"] if s["type"] == "assistant"] == [
        "Understood. I'm your helper."
    ]


def test_cursor_system_prompt_dropped_via_anchors():
    """With murder-owned anchors (system prompt + ground-truth user turn), the
    echoed prompt and the user echo are labelled ``user`` (dropped downstream),
    leaving only the genuine assistant turn. They must never surface as
    assistant chat."""
    frame = _cursor_frame_with_system_prompt()
    doc = transcripts.parse_frames(
        "cursor", [frame], system_prompt=_COLLAB_SYSTEM_PROMPT, user_texts=["test"]
    )
    kept = [s for s in doc["segments"] if s["type"] != "user"]
    assert kept == [
        {
            "type": "assistant",
            "phase": "final",
            "text": "Here — what do you want to work on?",
            "elapsed": None,
        },
    ]
    # Murder-owned content is present only as user segments, never assistant.
    asst_blob = json.dumps(kept, ensure_ascii=False)
    assert "generally assist the user" not in asst_blob
    assert "Murder keeps state" not in asst_blob
    assert any(s["type"] == "user" and s["text"] == "test" for s in doc["segments"])


def test_cursor_user_blocks_classified_by_colour_marker():
    """The primary signal: Cursor colour-codes user-input blocks. With the
    background-colour escapes preserved, user turns are recognised with no
    anchors supplied at all — and the system prompt never leaks as assistant."""
    frame = _cursor_paint_user_blocks(
        _cursor_frame_with_system_prompt(),
        [*_COLLAB_SYSTEM_PROMPT.split("\n\n"), "test"],
    )
    # Note: no system_prompt, no user_texts — colour alone must carry roles.
    doc = transcripts.parse_frames("cursor", [frame])
    kept = [s for s in doc["segments"] if s["type"] != "user"]
    assert kept == [
        {
            "type": "assistant",
            "phase": "final",
            "text": "Here — what do you want to work on?",
            "elapsed": None,
        },
    ]
    assert any(s["type"] == "user" and s["text"] == "test" for s in doc["segments"])
    assert "generally assist the user" not in json.dumps(kept, ensure_ascii=False)


def test_strip_leading_system_prompt_helper():
    strip = transcripts._strip_leading_system_prompt
    blocks = [
        ["para one"],
        ["para two"],
        ["test"],
        ["a reply"],
    ]
    prompt = "para one\n\npara two"
    assert strip(blocks, prompt) == [["test"], ["a reply"]]
    # No prompt / no match → blocks untouched.
    assert strip(blocks, None) == blocks
    assert strip(blocks, "something else entirely") == blocks
    # A partial match (prompt head present but not fully covered) is left intact
    # rather than dropping a real turn.
    assert strip([["para one"], ["test"]], "para one\n\npara two") == [
        ["para one"],
        ["test"],
    ]


def test_strip_tolerates_smart_quotes_and_rewrapping():
    """Cursor reflows the echoed prompt and may swap ASCII quotes for typographic
    ones; the match must survive both so it doesn't silently no-op in production."""
    strip = transcripts._strip_leading_system_prompt
    prompt = "the user's `plan.md` files must start with frontmatter"
    # Echoed back with curly quotes and a mid-paragraph soft wrap (extra block).
    blocks = [
        ["the user's `plan.md` files"],
        ["must start with frontmatter"],
        ["the real first message"],
    ]
    assert strip(blocks, prompt) == [["the real first message"]]


def test_real_collaborator_brief_is_stripped(tmp_path):
    """Ground the matcher against the *actual* assembled collaborator brief
    (collaborator.md + any repo context docs), not a hand-copied string."""
    from murder.bus import Role
    from murder.llm.harnesses.capabilities import HarnessCapabilities
    from murder.runtime.orchestration.brief import BriefContext, assembler_for

    ctx = BriefContext(
        role=Role.COLLABORATOR,
        repo_root=tmp_path,
        caps=HarnessCapabilities(),
        harness_name="cursor",
        model=None,
    )
    brief = assembler_for(ctx).build(ctx)
    assert "generally assist the user" in brief  # sanity: real prompt loaded

    import textwrap

    echoed_lines: list[str] = []
    for para in brief.split("\n\n"):
        for wrapped in textwrap.wrap(para.strip(), width=70) or [""]:
            echoed_lines.append(f"  {wrapped}")
        echoed_lines.append("")
    echoed = "\n".join(echoed_lines)

    frame = (
        "user@machine:~/Documents/code/murder $ agent\n\n\n"
        "  Cursor Agent\n"
        "  v2026.06.04-8f81907\n\n\n"
        f"{echoed}\n\n"
        "  what should we work on?\n\n\n"
        "  Here — tell me what to tackle.\n\n"
        " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
        "  → Add a follow-up\n"
        " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        "  Composer 2.5 · 7.3%                                    Auto-run\n"
        "  ~/Documents/code/murder · fix/shutdown-flock-race\n"
    )
    doc = transcripts.parse_frames(
        "cursor", [frame], system_prompt=brief, user_texts=["what should we work on?"]
    )
    kept = [s for s in doc["segments"] if s["type"] != "user"]
    assert kept == [
        {
            "type": "assistant",
            "phase": "final",
            "text": "Here — tell me what to tackle.",
            "elapsed": None,
        },
    ]
    # The real brief is anchored away — no paragraph of it survives as assistant.
    assert "generally assist the user" not in json.dumps(kept, ensure_ascii=False)
    assert any(
        s["type"] == "user" and s["text"] == "what should we work on?"
        for s in doc["segments"]
    )


def test_pi_system_prompt_stripped_when_supplied():
    """Pi shares the markerless alternation, so the same stripping applies."""
    prompt = "line one of the brief\n\nline two of the brief"
    frame = (
        "pi session\n"
        "\n"
        " line one of the brief\n"
        "\n"
        " line two of the brief\n"
        "\n"
        " what should we build?\n"
        "\n"
        " Let's start with the parser.\n"
        "\n"
        "> \n"
    )
    doc = transcripts.parse_frames("pi", [frame], system_prompt=prompt)
    users = [s["text"] for s in doc["segments"] if s["type"] == "user"]
    assert users == ["what should we build?"]
    blob = json.dumps(doc, ensure_ascii=False)
    assert "brief" not in blob
