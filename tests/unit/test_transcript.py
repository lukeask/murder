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
_HARNESS_PANES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
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


def test_cursor_leading_dot_cwd_banner_stripped_and_not_duplicated():
    """Cursor's cwd banner can render dot-first (``· ~/path``) instead of
    ``~/path · branch``. That shape slipped past the cwd-banner chrome rule and
    leaked into the transcript, *duplicating* around the first user turn
    (repainted above and below it). Pin that it is suppressed: the fixture brackets
    the user ``test`` turn with three copies of the banner, and the parse must
    contain none of them — only the clean user + assistant segments."""
    frames = _scenario_frames("cursor_cwd_banner")
    banner = "· ~/Documents/code/testingmurderharness"
    raw = "\n".join(frames)
    assert raw.count(banner) >= 3, "fixture must paint the leading-dot banner multiple times"

    doc = transcripts.parse_frames(
        "cursor", frames, user_texts=_FIXTURE_USER_TEXTS_BANNER
    )
    assert doc == _scenario_expected("cursor_cwd_banner")
    blob = json.dumps(doc, ensure_ascii=False)
    assert banner not in blob, "leading-dot cwd banner leaked into a parsed segment"
    # No duplication: exactly one user and one assistant segment survive.
    types = [s["type"] for s in doc["segments"]]
    assert types == ["user", "assistant"]


_FIXTURE_USER_TEXTS_BANNER = ["test"]


def test_cursor_flush_left_wrapped_reply_survives():
    """Cursor renders every real chat line with a 2-space left margin, but when an
    assistant reply is wider than the pane the terminal hard-wraps the overflow to
    the next physical row at column zero — where cursor cannot re-inject its
    margin. The chrome predicate used to drop *any* unindented line (``not
    line.startswith(" ")``), so the wrapped tail was discarded and, in narrow
    panes, whole assistant replies vanished from Verbose/Condensed even though they
    were present in the raw ``:tmux`` view (BUG-5). Pin that the flush-left wrapped
    tail is reassembled into the reply while the flush-left shell-prompt line
    (``user@host:path $ agent``) is still dropped."""
    frames = _scenario_frames("cursor_wrapped_reply")
    raw = "\n".join(frames)
    # The fixture genuinely contains a flush-left assistant continuation row and a
    # flush-left shell prompt, so the test exercises the real ambiguity.
    assert "\nterminal hard-wraps" in raw, "fixture must carry a flush-left wrapped tail"
    assert "$ agent" in raw, "fixture must carry the flush-left shell prompt"

    doc = transcripts.parse_frames("cursor", frames, user_texts=["test"])
    assert doc == _scenario_expected("cursor_wrapped_reply")
    assistant = [s for s in doc["segments"] if s["type"] == "assistant"]
    assert len(assistant) == 1, "the wrapped reply must be one assistant segment, not split"
    text = assistant[0]["text"]
    assert "terminal hard-wraps" in text, "flush-left wrapped tail dropped from the reply"
    assert "re-inject its two-space left margin" in text
    blob = json.dumps(doc, ensure_ascii=False)
    assert "$ agent" not in blob, "flush-left shell prompt leaked into a segment"


def test_cursor_prose_code_reply_full_doc():
    """Round-3 regression: a cursor reply with explanatory prose followed by a
    multi-line python example, bracketed by a startup ``shift+tab`` hint and the
    *bare* cwd banner (``~/path`` with no ``· branch`` suffix). All three round-3
    symptoms in one fixture: (1) prose must survive, (2) code newlines preserved,
    (3) hint + bare banner suppressed."""
    frames = _scenario_frames("cursor_prose_code")
    user_text = "explain in 3 sentences what a hash map is, then write a 6-line python example"
    doc = transcripts.parse_frames("cursor", frames, user_texts=[user_text])
    assert doc == _scenario_expected("cursor_prose_code")


def test_cursor_prose_code_symptoms_localized():
    """Localize each round-3 symptom for failure triage."""
    frames = _scenario_frames("cursor_prose_code")
    user_text = "explain in 3 sentences what a hash map is, then write a 6-line python example"
    doc = transcripts.parse_frames("cursor", frames, user_texts=[user_text])
    assistants = [s["text"] for s in doc["segments"] if s["type"] == "assistant"]
    blob = json.dumps(doc, ensure_ascii=False)

    # (1) prose present — the 3-sentence explanation, de-wrapped to one line.
    prose = next((t for t in assistants if t.startswith("A hash map is")), None)
    assert prose is not None, "assistant prose was dropped"
    assert "\n" not in prose, "soft-wrapped prose must be de-wrapped"
    assert "chaining or open addressing" in prose

    # (2) code newlines preserved — the two statements stay on separate lines.
    code = next((t for t in assistants if t.startswith("scores = {")), None)
    assert code is not None, "code block was dropped"
    assert code == 'scores = {"alice": 95, "bob": 87, "carol": 92}\nprint(scores["alice"])  # lookup by key', (
        "code lines were merged onto one line (newlines lost)"
    )

    # (3) chrome suppressed — neither the shift+tab hint nor the bare cwd banner.
    assert "shift+tab" not in blob, "startup plan-mode hint leaked as content"
    assert "enable Plan Mode" not in blob
    assert "~/Documents/code/testingmurderharness" not in blob, "bare cwd banner leaked"
    types = [s["type"] for s in doc["segments"]]
    assert types == ["user", "assistant", "assistant"], f"unexpected segments: {types}"


def test_cursor_bare_cwd_banner_chrome_predicate_spares_prose():
    """The bare-cwd-banner rule keys on a line that is *only* a rooted path token;
    real prose mentioning a path (multi-token) must stay content."""
    from murder.llm.harnesses.transcripts.grammar.cursor import _CURSOR_CWD_BARE_RE

    assert _CURSOR_CWD_BARE_RE.match("  ~/Documents/code/testingmurderharness")
    assert _CURSOR_CWD_BARE_RE.match("/var/log")
    # multi-token prose about a path is NOT the banner
    assert not _CURSOR_CWD_BARE_RE.match("  ~/foo is the wrong directory")
    assert not _CURSOR_CWD_BARE_RE.match("  edit /etc/hosts then restart")
    # leading-dot banner shape is handled by its own rule, not this one
    assert not _CURSOR_CWD_BARE_RE.match("  this is a normal sentence")


def test_cursor_slash_command_palette_stripped():
    """Cursor's ``/`` command palette renders as an overlay above the input and
    repaints as you type, so its rows leak into the scrollback *and* duplicate
    (the same /command appearing 2-3 times). Each row is ``/command`` + a 2+-space
    column gap + a description. Pin that the palette is suppressed while genuine
    assistant prose discussing the same binds survives."""
    frame = (
        "  Here is the binds roundup you asked for.\n"
        "  mod+ is your configured command modifier (default alt).\n"
        "\n"
        "  · /create-rule       Create Cursor rules for persistent AI guidance. Use it\n"
        "  · /babysit           Keep a PR merge-ready by triaging comments, resolving\n"
        "  · /create-rule       Create Cursor rules for persistent AI guidance. Use it\n"
        "  clear… /create-rule  Create Cursor rules for persistent AI guidance.\n"
    )
    doc = transcripts.parse_frames("cursor", [frame])
    blob = json.dumps(doc, ensure_ascii=False)
    assert "/create-rule" not in blob, f"slash palette leaked: {blob}"
    assert "/babysit" not in blob, f"slash palette leaked: {blob}"
    # Genuine assistant prose about the binds is kept.
    assert "binds roundup you asked for" in blob
    assert "configured command modifier" in blob


def test_cursor_slash_palette_rule_spares_paths_and_inline_mentions():
    """The palette rule keys on a 2+-space column gap after a ``/command`` at a
    word boundary, so file paths (``src/main``) and single-spaced inline command
    mentions (``/help to reset``) must never be mistaken for palette chrome."""
    from murder.llm.harnesses.transcripts.grammar.cursor import _CURSOR_SLASH_PALETTE_RE

    assert not _CURSOR_SLASH_PALETTE_RE.search("  edit src/main  then rebuild it")
    assert not _CURSOR_SLASH_PALETTE_RE.search("  run /help to reset the session")
    assert _CURSOR_SLASH_PALETTE_RE.search("  · /babysit           Keep a PR merge-ready")


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


def test_cc_choice_prompt_multiselect_fixture_matches_expected():
    # Real haiku CC capture of an AskUserQuestion multiSelect menu: frame 0 has
    # nothing checked (cursor on 1), frame 1 has Mushroom toggled (cursor on 2),
    # frame 2 has Cheese+Mushroom checked with the cursor on the unnumbered
    # Submit row (selected: None — the dialog must stay live, not resolve).
    doc = transcripts.parse_frames("claude_code", _scenario_frames("cc_mc_multiselect"))
    assert doc == _scenario_expected("cc_mc_multiselect")
    prompt = doc["segments"][-1]
    assert prompt["type"] == "choice_prompt"
    assert prompt["multi"] is True
    assert prompt["selected"] is None
    assert prompt["answered"] is False
    checked = [o["number"] for o in prompt["options"] if o["checked"]]
    assert checked == [1, 2]
    assert doc["state"] == "awaiting_approval"


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


def test_cursor_blank_braille_spinner_is_chrome():
    # Cursor animates its busy spinner with a leading BRAILLE BLANK cell, e.g.
    # "⠀⠞ Editing  9.67k tokens" / "⠀⠞ Calling  10.21k tokens". U+2800 sat just
    # below the spinner glyph range, so these frames leaked into assistant text.
    from murder.llm.harnesses.parsing import is_status_spinner_line

    for frame in ("⠀⠞ Editing  9.67k tokens", " ⠀⠞ Calling  10.21k tokens"):
        assert is_status_spinner_line(frame), f"spinner frame not detected: {frame!r}"
    # The tool-activity rollups that share those verbs must still be kept.
    for keep in ("Editing usageSelectors.ts", "Edited usageSelectors.ts   +1 -1"):
        assert not is_status_spinner_line(keep), f"rollup wrongly flagged: {keep!r}"


def test_cursor_tool_rollups_become_collapsed_tool_calls():
    # Cursor paints tool activity as rollup lines that progressively redraw. They
    # must parse as tool_call segments, each redraw chain collapsed to its final
    # frame, distinct operations kept separate, and narration left as prose.
    from murder.llm.harnesses.transcripts.grammar.cursor import parse_lines

    scrollback = [
        " Searching the codebase for where reset times are formatted.",
        "",
        ' Grepping, searching 1 grep, 1 search Grepped "reset" in .',
        "",
        ' Grepped, searched 1 grep, 1 search Grepped "reset" in . Searched "x" in .',
        "",
        " Editing usageSelectors.ts",
        "",
        " Edited usageSelectors.ts   +9 -1",
        "",
        " $ cd /repo && npm test",
        "",
        " All 15 tests pass; report written.",
    ]
    segs = parse_lines(scrollback)
    kinds = [s["type"] for s in segs]
    assert kinds == ["assistant", "tool_call", "tool_call", "tool_call", "assistant"]
    # Grep redraw chain collapsed to its final (past-tense) frame, not the gerund.
    assert segs[1]["title"].startswith("Grepped, searched")
    # Editing -> Edited (same file) collapsed; shell command stays its own call.
    assert segs[2]["title"] == "Edited usageSelectors.ts +9 -1"
    assert segs[3]["title"] == "cd /repo && npm test"
    # The leading narration ("Searching the codebase ...") is not a tool call.
    assert segs[0]["text"].startswith("Searching the codebase")


def test_cursor_running_shell_repaints_collapse_to_one_tool_call():
    # A running shell rollup carries a live footer ("9.5s in inktui  ctrl+b twice
    # to send to background") whose timer ticks on every repaint, and each repaint
    # lands on a new scrollback line. The footer must be stripped so the frames
    # dedupe to ONE tool_call instead of one block per tick.
    from murder.llm.harnesses.transcripts.grammar.cursor import parse_lines

    scrollback = [
        " $ npm test 2>&1 | tail -15  8.5s in inktui  ctrl+b twice to send to background",
        "",
        " $ npm test 2>&1 | tail -15  9.0s in inktui  ctrl+b twice to send to background",
        "",
        " $ npm test 2>&1 | tail -15  9.5s in inktui  ctrl+b twice to send to background",
        "",
        " All 15 tests pass.",
    ]
    segs = parse_lines(scrollback)
    assert [s["type"] for s in segs] == ["tool_call", "assistant"]
    tool = segs[0]
    assert tool["title"] == "npm test 2>&1 | tail -15"
    assert tool["result"] == "$ npm test 2>&1 | tail -15"
    assert tool["running"] is True
    # A finished frame (no footer) still continues the same command's chain.
    finished = parse_lines([*scrollback[:-1], " $ npm test 2>&1 | tail -15"])
    shell = [s for s in finished if s["type"] == "tool_call"]
    assert len(shell) == 1
    assert shell[0]["running"] is False
    # A command that genuinely ends in a duration token is NOT stripped.
    sleeper = parse_lines([" $ sleep 5s"])
    assert sleeper[0]["result"] == "$ sleep 5s"


def test_cursor_tool_rollup_spares_narration():
    from murder.llm.harnesses.transcripts.grammar.cursor import _is_cursor_tool_rollup

    for prose in (
        "Searching the codebase for where reset times are formatted",
        "Updating formatMinutes to use d/h for long resets and adding tests",
        "Writing the report from the bindings registry and per-pane keymaps",
    ):
        assert not _is_cursor_tool_rollup(prose), f"narration misread as tool: {prose!r}"
    for rollup in (
        'Grepped, searched 1 grep, 1 search Grepped "reset" in .',
        "Edited allctrlbinds.md   +26",
        "Read, grepped, globbed 17 files, 15 greps, 1 glob … 30 earlier items hidden",
    ):
        assert _is_cursor_tool_rollup(rollup), f"rollup missed: {rollup!r}"


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
# Codex-specific: the "update available" menu is chrome, not a user turn.
# --------------------------------------------------------------------------- #
def test_codex_no_update_menu_in_segments():
    from murder.llm.harnesses.transcripts.grammar import codex as codex_grammar

    menu_lines = [
        "  ✨ Update available! 0.139.0 -> 0.141.0",
        "",
        "  Release notes: https://github.com/openai/codex/releases/latest",
        "",
        "› 1. Update now (runs `npm install -g @openai/codex`)",
        "  2. Skip",
        "  3. Skip until next version",
        "",
        "  Press enter to continue",
    ]
    segments = codex_grammar.parse_lines(menu_lines)
    assert not any(s["type"] == "user" for s in segments), segments
    blob = json.dumps(segments, ensure_ascii=False)
    for needle in ("Update now", "npm install", "Update available"):
        assert needle not in blob, f"codex update menu leaked: {needle!r}"


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
# Antigravity 1.0.10 tool-use turn (BUG-11 / BUG-12). Live capture: the harness
# emits `● ToolName(arg)` tool calls, then a final prose reply, and while
# generating paints `<braille>  Loading...` plus a `└ Tip:` hint. The grammar
# must (a) surface each tool call as a discrete tool_call segment, (b) keep the
# final reply as assistant prose, and (c) NEVER leak the spinner / tip lines into
# a segment — a leaked spinner becomes the frozen "Working…" block that never
# clears once it scrolls above the live window.
# --------------------------------------------------------------------------- #
def _agy_tool_use_lines() -> list[str]:
    return [
        "> Locate and read prize.txt and report the prize word",
        "▸ Thought for 1s",
        "  Prioritizing Tool Usage",
        "● ListDir(/home/luke/x)",
        "● Bash(find . -name prize.txt)",
        "● Read(/home/luke/x/prize.txt)",
        "  The prize file has been located at prize.txt.",
        "  The prize word is: Pharsalus",
        "⡿  Loading...",
        "└ Tip: Use /fork to branch the conversation from an earlier point.",
    ]


def test_antigravity_tool_calls_rendered_as_segments():
    from murder.llm.harnesses.transcripts.grammar import antigravity as ag

    segs = ag.parse_lines(_agy_tool_use_lines())
    tool_titles = [s["title"] for s in segs if s["type"] == "tool_call"]
    assert tool_titles == [
        "ListDir(/home/luke/x)",
        "Bash(find . -name prize.txt)",
        "Read(/home/luke/x/prize.txt)",
    ]


def test_antigravity_final_reply_preserved_and_spinner_not_leaked():
    from murder.llm.harnesses.transcripts.grammar import antigravity as ag

    segs = ag.parse_lines(_agy_tool_use_lines())
    ag.close_last_turn(segs)
    assistant_texts = [s["text"] for s in segs if s["type"] == "assistant"]
    # The final reply text is captured...
    assert any("The prize word is: Pharsalus" in t for t in assistant_texts), assistant_texts
    # ...and the last assistant block is sealed final at idle.
    finals = [s for s in segs if s["type"] == "assistant" and s["phase"] == "final"]
    assert any("Pharsalus" in s["text"] for s in finals)
    # The spinner ("Loading...") and the `└ Tip:` hint must never leak.
    blob = json.dumps(segs, ensure_ascii=False)
    for needle in ("Loading...", "Tip:", "⡿"):
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
    # ...and it must not leak as an assistant turn either. Suppressing only the
    # user branch left the line to fall through to the bare-prose branch, which
    # re-emitted it as a phantom assistant segment carrying the literal `❯`.
    slash_blob = json.dumps(slash, ensure_ascii=False)
    assert "/model opus" not in slash_blob, f"slash echo leaked: {slash_blob}"
    assert "❯" not in slash_blob, f"prompt glyph leaked: {slash_blob}"

    clear = cc.parse_lines(["❯ /clear", "● ok"])
    assert [s for s in clear if s["type"] == "user"] == []
    assert "/clear" not in json.dumps(clear, ensure_ascii=False)

    real = cc.parse_lines(["❯ what does this function do?", "● ok"])
    real_users = [s for s in real if s["type"] == "user"]
    assert len(real_users) == 1
    assert "what does this function" in real_users[0]["text"]


def test_cc_usage_dialog_lines_are_chrome():
    """``/usage`` modal rows (session/week bars, reset prose, boxed scrollback)
    must not leak into transcript segments when projection races the overlay."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    wide = _strip_frame_header(
        (_HARNESS_PANES / "cc_usage_dialog_wide.txt").read_text(encoding="utf-8")
    )
    scrollback = _strip_frame_header(
        (_HARNESS_PANES / "cc_usage_scrollback.txt").read_text(encoding="utf-8")
    )
    needles = (
        "Current session",
        "Current week",
        "% used",
        "Resets ",
        "Total cost:",
        "What's contributing to your limits usage?",
        "Claude Code – Usage",
        "/usage",
        "Esc to cancel",
    )
    for label, dialog in (("wide", wide), ("scrollback", scrollback)):
        mixed = ["❯ hi", "● working", *dialog.splitlines(), "● done"]
        blob = json.dumps(cc.parse_lines(mixed), ensure_ascii=False)
        leaked = [n for n in needles if n in blob]
        assert not leaked, f"{label} /usage dialog leaked {leaked}: {blob}"

    # Indented rows can land inside an in-flight ● block — filter those too.
    race = [
        "❯ hi",
        "● working",
        "  Current session",
        "  ████████████████████████████████████████████       88% used",
        "  Resets 1:40pm (America/New_York)",
        "● done",
    ]
    race_blob = json.dumps(cc.parse_lines(race), ensure_ascii=False)
    assert "Current session" not in race_blob and "88% used" not in race_blob
    assistants = [s["text"] for s in cc.parse_lines(race) if s["type"] == "assistant"]
    assert assistants == ["working", "done"]


def test_cc_usage_prose_mentioning_percent_used_survives():
    """Conservative /usage filtering must not eat assistant prose that merely
    mentions a percentage."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    segs = cc.parse_lines(
        ["❯ q", "● The metric is 88% used already in practice — not a bar row."]
    )
    blob = json.dumps(segs, ensure_ascii=False)
    assert "88% used already" in blob, f"prose wrongly dropped: {blob}"


def test_agy_usage_dialog_lines_are_chrome():
    """Antigravity ``/usage`` quota rows (per-model and grouped weekly limits)
    must not leak into assistant segments when the modal races projection."""
    from murder.llm.harnesses.transcripts.grammar import antigravity as ag

    per_model = [
        "> check usage",
        "└ Model Quota",
        "  Gemini 3.5 Flash (Medium)",
        "  ███████████ ███████████ 100%",
        "  Quota available",
        "  Claude Sonnet 4.6 (Thinking)",
        "  20% remaining · Refreshes in 12h 39m",
        "> next",
    ]
    grouped = [
        "> usage",
        "└ Models & Quota",
        "GEMINI MODELS",
        "  Models within this group: Gemini Flash, Gemini Pro",
        "  Weekly Limit",
        "    [███████████████████████████████████████████░░░░░░░] 85.61%",
        "  86% remaining · Refreshes in 157h 26m",
        "> next",
    ]
    needles = (
        "Model Quota",
        "Models & Quota",
        "Gemini 3.5 Flash (Medium)",
        "Quota available",
        "remaining · Refreshes",
        "GEMINI MODELS",
        "Weekly Limit",
        "85.61%",
    )
    for label, lines in (("per-model", per_model), ("grouped", grouped)):
        blob = json.dumps(ag.parse_lines(lines), ensure_ascii=False)
        leaked = [n for n in needles if n in blob]
        assert not leaked, f"agy {label} /usage dialog leaked {leaked}: {blob}"


def test_codex_status_limit_lines_are_chrome():
    """Codex ``/status`` limit rows must not leak into transcript segments."""
    from murder.llm.harnesses.transcripts.grammar import codex as cx

    # codex_usage_limit.txt is a usage-cap message, not /status rows; limit/reset
    # shapes live in codex_status_scrollback.txt (same recording family).
    _strip_frame_header((_HARNESS_PANES / "codex_usage_limit.txt").read_text(encoding="utf-8"))
    status = _strip_frame_header(
        (_HARNESS_PANES / "codex_status_scrollback.txt").read_text(encoding="utf-8")
    )
    limit_lines = [ln for ln in status.splitlines() if "limit:" in ln.lower()][:2]
    needles = ("5h limit:", "Weekly limit:", "% left (resets", "/status")
    for label, dialog_lines in (
        ("scrollback", limit_lines),
        (
            "race",
            [
                "│  5h limit:             [░░░░░░░░░░░░░░░░░░░░] 0% left (resets 20:43)            │",
                "  Weekly limit:         [█████████░░░░░░░░░░░] 43% left (resets 16:54 on 30 May)",
            ],
        ),
    ):
        mixed = ["› hi", "• working", *dialog_lines, "• done"]
        blob = json.dumps(cx.parse_lines(mixed), ensure_ascii=False)
        leaked = [n for n in needles if n in blob]
        assert not leaked, f"codex {label} /status leaked {leaked}: {blob}"
    assistants = [s["text"] for s in cx.parse_lines(mixed) if s["type"] == "assistant"]
    assert assistants == ["working", "done"]


def test_cc_bare_responding_dot_is_chrome():
    """A lone ``●`` (CC's live "responding" indicator, no trailing text) is chrome,
    not content. ``_CC_BULLET_RE`` requires text after the glyph, so a bare ``●``
    matched no rule and leaked through the bare-prose branch as a phantom assistant
    segment containing just ``●``."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    # The bare dot leaks two ways: as its own segment, and (when it lands between
    # blank lines inside a block) absorbed as a phantom continuation line. Both the
    # column-0 form and a leading-whitespace form must be dropped.
    segs = cc.parse_lines(
        ["❯ hi", "", "●", "", "● real answer", "  more text", "", "  ●", "", "● tail"]
    )
    assert "●" not in json.dumps(segs, ensure_ascii=False), f"bare dot leaked: {segs}"
    assistant = [s for s in segs if s["type"] == "assistant"]
    assert [a["text"] for a in assistant] == ["real answer more text", "tail"]


def test_cc_apostrophe_gerund_spinner_is_chrome():
    """CC's whimsical spinner words include elided gerunds (``Beboppin'``,
    ``Jivin'``) whose apostrophe falls outside ``\\w``. The single-word gerund
    class dropped them, so every animation frame leaked as a phantom assistant
    turn (one per second, stacking with climbing token counts)."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    frames = [
        "✳ Beboppin'…",
        "✳ Beboppin'… (2s · thinking with medium effort)",
        "* Beboppin'… (3s · ↓ 76 tokens · thinking with medium effort)",
        "· Jivin'… (17s · ↓ 902 tokens)",
    ]
    segs = cc.parse_lines(["❯ go", "● on it", *frames])
    blob = json.dumps(segs, ensure_ascii=False)
    assert "Beboppin" not in blob and "Jivin" not in blob, f"spinner leaked: {blob}"


def test_cc_tip_footer_wrapped_tail_is_chrome():
    """CC's bottom-of-pane ``⎿ Tip:`` footer (under the working spinner) soft-wraps,
    and the live footer wraps its tail back to column 0 — so a lone ``/config`` from
    "…enable push notifications in /config" fell into the bare-prose branch as a
    phantom assistant turn. The Tip line plus its wrapped tail must be consumed as
    one chrome unit, whether the tail wraps to column 0 or stays indented."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    # Tail wrapped to column 0 (the live-footer rendering that triggered the bug).
    col0 = cc.parse_lines(
        [
            "● I'll read that report.",
            "",
            "  Searching for 1 pattern, reading 1 file… (ctrl+o to expand)",
            "  ⎿  .murder/reports/freshstartupjun20.md",
            "",
            "· Beboppin'… (38s · ↑ 195 tokens)",
            "  ⎿  Tip: Get pinged on your phone when long tasks finish · enable push notifications in",
            "/config",
        ]
    )
    blob = json.dumps(col0, ensure_ascii=False)
    assert "/config" not in blob, f"tip wrap leaked: {blob}"
    assert [s["text"] for s in col0 if s["type"] == "assistant"] == [
        "I'll read that report."
    ]

    # Tail directly under a bullet with an indented wrap (collector path).
    indented = cc.parse_lines(
        [
            "● Done.",
            "",
            "· Beboppin'… (4s · ↑ 12 tokens)",
            "  ⎿  Tip: Get pinged on your phone when long tasks finish · enable push notifications in",
            "     /config",
        ]
    )
    assert "/config" not in json.dumps(indented, ensure_ascii=False)
    assert [s["text"] for s in indented if s["type"] == "assistant"] == ["Done."]

    # Guard the red-team regression: a real ⎿ tool RESULT row that merely contains
    # the word "Tip:" is NOT the footer — its content must survive. A tool result
    # follows a ``● Verb(...)`` header and is gathered by _cc_collect_result via
    # _CC_RESULT_RE, never by the Tip branch.
    result = cc.parse_lines(
        [
            "● Bash(grep -rn Tip README.md)",
            "  ⎿  README.md:5: Tip: run the linter first",
            "     README.md:9: Tip: commit often",
        ]
    )
    blob = json.dumps(result, ensure_ascii=False)
    assert "run the linter first" in blob and "commit often" in blob


def test_cc_thinking_effort_spinner_is_chrome():
    """Spinner status lines whose parenthetical carries only a thinking-effort
    tail (``(5s · thinking with high effort)`` — no token counts, no ``esc to``)
    must be suppressed, including the double-glyph ``· ✻`` rendering. Regression:
    these leaked through the bare-prose branch as phantom assistant turns."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    spinners = [
        "· ✻ Scampering… (5s · thinking with high effort)",
        "✻ Scampering… (6s · thinking with high effort)",
        "* Simmering… (1s · thinking with medium effort)",
        "· · Scampering… (5s · thinking with high effort)",
    ]
    for spinner in spinners:
        segs = cc.parse_lines(["❯ hello", "● working on it", spinner])
        blob = json.dumps(segs, ensure_ascii=False)
        assert "Scampering" not in blob and "Simmering" not in blob, (
            f"spinner leaked into parse: {spinner!r}"
        )


def test_cc_multiword_status_spinner_is_chrome():
    """Newer CC builds emit a contextual multi-word status phrase
    (``Updating sizing and tests…``) instead of a single gerund. The animated
    frames repaint every second, so when they leaked they flooded the transcript
    with dozens of near-identical phantom assistant turns. Regression: the spinner
    regex only matched single-word ``[A-Z][\\w-]+…`` status text."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    frames = [
        "· ✶ Updating sizing and tests… (11m 5s · ↓ 49.6k tokens)",
        "· · Updating sizing and tests… (11m 6s · ↓ 49.6k tokens)",
        "✻ Updating sizing and tests… (11m 7s · ↓ 49.7k tokens)",
        "· ✽ Running the unit suite… (2m 1s · ↑ 3.2k tokens)",
    ]
    segs = cc.parse_lines(["❯ go", "● on it", *frames])
    blob = json.dumps(segs, ensure_ascii=False)
    assert "Updating sizing and tests" not in blob, f"multiword spinner leaked: {blob}"
    assert "Running the unit suite" not in blob, f"multiword spinner leaked: {blob}"


def test_cc_multiword_prose_ending_in_ellipsis_survives():
    """The multi-word spinner widening must not eat a real assistant sentence that
    happens to be a line of capitalised words ending in ``…`` — it has no leading
    spinner glyph, so it is prose, not chrome."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    segs = cc.parse_lines(["❯ hi", "● Looking at the failing tests now…"])
    blob = json.dumps(segs, ensure_ascii=False)
    assert "Looking at the failing tests now" in blob, f"prose dropped: {blob}"


def test_cc_chrome_substrings_dont_eat_real_continuations():
    """The 'to manage' / 'Waiting for' chrome rules are anchored to their actual
    UI shapes (`↓ to manage`, `✻ Waiting for N background agents`). Plain English
    uses of those phrases on wrapped continuation lines must survive. Regression:
    bare substring matching dropped real user/assistant lines."""
    from murder.llm.harnesses.transcripts.grammar import claude_code as cc

    segs = cc.parse_lines(["❯ I built a tool", "  that I use to manage worktrees", "● ok"])
    users = [s for s in segs if s["type"] == "user"]
    assert len(users) == 1 and "to manage worktrees" in users[0]["text"]

    segs = cc.parse_lines(["❯ hi", "● ok then", "  Waiting for your reply on the design question."])
    asst = [s["text"] for s in segs if s["type"] == "assistant"]
    assert any("Waiting for your reply" in t for t in asst)

    # The real chrome lines are still suppressed.
    chrome_frame = [
        "❯ hi",
        "● ok",
        "✻ Waiting for 1 background agent to finish",
        "  ⎿  Backgrounded agent (↓ to manage · ctrl+o to expand)",
    ]
    blob = json.dumps(cc.parse_lines(chrome_frame), ensure_ascii=False)
    assert "background agent" not in blob
    assert "↓ to manage" not in blob


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


def _cursor_paint_user_blocks_black(frame: str, user_texts: list[str]) -> str:
    """Re-create Cursor's current plain black-background user row shape."""
    needles = {t.strip() for t in user_texts}
    out: list[str] = []
    for line in frame.splitlines():
        if line.strip() in needles:
            out.append(f"\x1b[40m{line}\x1b[49m")
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


def test_cursor_black_background_input_box_continuation_is_chrome():
    """Cursor v2026.07 paints the composer with plain SGR 40m, not RGB bg."""
    frame = (
        "  Understood. I'm your helper.\n"
        "\n"
        "\x1b[40m \x1b[2m→ \x1b[0m\x1b[40mYou are the user's general-purpose helper. Your role in the\x1b[49m\n"
        "\x1b[40m   system is to generally assist the user however they ask.\x1b[49m\n"
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


def test_cursor_dirty_user_anchor_drops_clean_suffix_echo():
    """If a sent message contains copied murder TUI chrome, Cursor can echo only
    the clean prose suffix. In no-ANSI degraded mode that suffix must still be
    recognised as user-authored content, not appended as assistant output."""
    dirty_user = (
        "╰─ Codex ◇ GPT-5.5 ───────── main ─╯"
        "┗━ Cursor ━━━━━━━━━━━━━━━━━━━ main ━┛ "
        "is the bottom lines of current murder TUI, note that codex has model "
        "sohown but cursor-cli does not have the model shown. Please investigate why"
    )
    clean_echo = (
        "current murder TUI, note that codex has model sohown but cursor-cli does "
        "not have the model shown. Please investigate why"
    )
    frame = (
        f"  {clean_echo}\n\n"
        "  The footer reads the roster model, not Cursor's live status bar.\n\n"
        "  Composer 2.5 · 7.3%                                    Auto-run\n"
        "  ~/Documents/code/murder · main\n"
    )

    doc = transcripts.parse_frames("cursor", [frame], user_texts=[dirty_user])

    assert [s["text"] for s in doc["segments"] if s["type"] == "assistant"] == [
        "The footer reads the roster model, not Cursor's live status bar."
    ]
    assert any(s["type"] == "user" and s["text"] == clean_echo for s in doc["segments"])


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


def test_cursor_user_blocks_classified_by_black_background_marker():
    """Cursor v2026.07 uses SGR 40m for submitted user rows."""
    frame = _cursor_paint_user_blocks_black(
        _cursor_frame_with_system_prompt(),
        [*_COLLAB_SYSTEM_PROMPT.split("\n\n"), "test"],
    )
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
    from murder.runtime.agents.types import AgentRole
    from murder.llm.harnesses.capabilities import HarnessCapabilities
    from murder.runtime.orchestration.brief import BriefContext, assembler_for

    ctx = BriefContext(
        role=AgentRole.COLLABORATOR,
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
