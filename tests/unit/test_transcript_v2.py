"""Ground-truth tests for the NEW (v2) harness transcript parsing stack.

================================================================================
DISEMBODIED TESTS — the parser they pin does not exist yet.
================================================================================
These tests are written *before* the implementation, against the ground-truth
fixtures in ``tests/fixtures/transcripts/`` (see that dir's ``SCHEMA.md``). They
describe the contract the v2 rewrite must satisfy and will replace the legacy
flat ``(role, text)`` model in ``murder/llm/harnesses/transcripts.py`` /
``tests/unit/test_harness_transcripts.py``.

Until ``murder.llm.harnesses.transcript_v2`` lands, the whole module SKIPS (via the
``importorskip`` below) so the suite stays green. The plan that tracks the
rewrite is ``.murder/plans/plan-transcript-parser-v2.md``.

Assumed v2 API (this is the seam the rewrite must expose; rename here + in the
plan if the implementer chooses different names):

    from murder.llm.harnesses import transcript_v2

    # stateful, appending: feed pane captures in order, read the accumulated doc
    acc = transcript_v2.TranscriptAccumulator(harness="claude_code")
    for frame in frames:
        acc.feed(frame)
    doc = acc.to_dict()        # -> TranscriptDoc dict matching SCHEMA.md

    # convenience: feed a whole sequence at once
    doc = transcript_v2.parse_frames("claude_code", frames)

``doc`` is the discriminated-union document defined in SCHEMA.md:
``{"harness","state","condensed","segments":[...]}``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

transcript_v2 = pytest.importorskip(
    "murder.llm.harnesses.transcript_v2",
    reason="parser v2 not implemented yet — disembodied ground-truth tests",
)

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
_HARNESSES = ["cc", "codex"]
_HARNESS_KIND = {"cc": "claude_code", "codex": "codex"}

# Chrome / live-UI strings that must NEVER survive into a parsed segment.
_CHROME_NEVER = [
    "bypass permissions",
    "esc to interrupt",
    "ctrl+o to expand",
    "ctrl+t to view transcript",
    "Tip:",
    "tokens",
    "shift+tab to cycle",
    "Find and fix a bug in @filename",
]


def _frames(harness: str) -> list[str]:
    fdir = _FIXTURES / harness / "frames"
    return [p.read_text(encoding="utf-8") for p in sorted(fdir.glob("*.txt"))]


def _expected(harness: str) -> dict:
    return json.loads((_FIXTURES / harness / "expected.json").read_text(encoding="utf-8"))


def _parse(harness: str) -> dict:
    if not hasattr(transcript_v2, "parse_frames"):
        pytest.skip("transcript_v2.parse_frames not implemented yet")
    return transcript_v2.parse_frames(_HARNESS_KIND[harness], _frames(harness))


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
    # the terminal final of each turn carries the completion-marker duration
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
    assert segs[finals[0]]["elapsed"] == "13m 06s"


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
    if not hasattr(transcript_v2, "TranscriptAccumulator"):
        pytest.skip("transcript_v2.TranscriptAccumulator not implemented yet")
    return transcript_v2.TranscriptAccumulator(_HARNESS_KIND[harness])


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
