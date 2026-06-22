"""Phase-1 structure-preserving reflow: block classifier + de-wrap-prose-only.

The readability engine's foundation (see .murder/plans/TUIchat-1-parser-reflow.md):
``reflow_paragraphs`` must de-wrap only *confident prose* and preserve everything
else (code fences, space-aligned tables, indented blocks, lists) verbatim, while
staying deterministic across streaming redraw frames so content-key dedup holds.
"""

from __future__ import annotations

import murder.llm.harnesses.transcripts as transcripts
from murder.llm.harnesses.transcripts._shared import (
    classify_block,
    reflow_paragraphs,
)

_ID = lambda line: line.rstrip()  # noqa: E731 — identity dedent for raw-line tests


def _reflow(lines: list[str], **kw) -> str:
    return reflow_paragraphs(lines, dedent=_ID, preserve_prefixes=(), **kw)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def test_classify_wrapped_prose_is_prose():
    assert classify_block(["a long sentence that the", "harness soft-wrapped"]) == "prose"


def test_classify_space_aligned_table_is_pre():
    # No box-drawing glyphs; columns aligned purely by runs of spaces. This is the
    # case the old allowlist crushed.
    table = ["Harness     Status", "claude_code OK", "codex       broken", "cursor      ready"]
    assert classify_block(table) == "pre"


def test_classify_uniform_indent_is_pre():
    assert classify_block(["  indented one", "  indented two"]) == "pre"


def test_classify_box_drawing_is_pre():
    assert classify_block(["┌────┐", "│ hi │", "└────┘"]) == "pre"


def test_classify_bullets_and_numbers_are_list():
    assert classify_block(["- first", "- second"]) == "list"
    assert classify_block(["1. first", "2. second"]) == "list"
    assert classify_block(["* star", "* bullet"]) == "list"


# --------------------------------------------------------------------------- #
# De-wrap prose only; preserve everything else verbatim
# --------------------------------------------------------------------------- #
def test_prose_dewraps_to_single_line():
    out = _reflow(["this was", "soft", "wrapped"])
    assert out == "this was soft wrapped"
    assert "\n" not in out


def test_columnar_table_preserved_verbatim():
    table = ["Harness     Status", "claude_code OK", "cursor      ready"]
    out = _reflow(table)
    assert out == "\n".join(table)  # internal column spaces survive intact


def test_fenced_code_preserved_including_inner_blanks_and_indent():
    lines = ["intro prose here", "", "```python", "def f():", "", "    return 1", "```", "", "outro"]
    out = _reflow(lines)
    assert "```python\ndef f():\n\n    return 1\n```" in out
    assert out.startswith("intro prose here")
    assert out.endswith("outro")


def test_mixed_blocks_collapse_prose_keep_table():
    lines = [
        "here is a wrapped",
        "explanation paragraph",
        "",
        "Col1    Col2",
        "aaa     bbb",
        "ccc     ddd",
    ]
    out = _reflow(lines)
    para, table = out.split("\n\n")
    assert para == "here is a wrapped explanation paragraph"
    assert table == "Col1    Col2\naaa     bbb\nccc     ddd"


def test_blocks_joined_with_blank_line():
    out = _reflow(["para one line", "", "para two line"])
    assert out == "para one line\n\npara two line"


def test_real_model_list_columns_survive():
    # Shape drawn from a real cursor model-picker capture
    # (tests/fixtures/harness_panes/cursor_model_list.txt): three space-aligned
    # columns, no box-drawing. The old allowlist crushed this to one line.
    block = [
        "Auto",
        "Composer 2.5             (Tab to modify)",
        "GPT-5.5                  272K Medium",
        "Sonnet 4.6               200K High",
    ]
    out = _reflow(block)
    assert out == "\n".join(block)
    assert "272K Medium" in out and "200K High" in out


# --------------------------------------------------------------------------- #
# Determinism / dedup gate — output is a pure function of the input lines, and a
# growing block's earlier render is a prefix of its later render, so
# ``_is_streaming_extension`` (and thus content-key dedup) holds across frames.
# --------------------------------------------------------------------------- #
def test_reflow_is_pure_function():
    lines = ["Col1    Col2", "aaa     bbb", "ccc     ddd"]
    assert _reflow(lines) == _reflow(lines) == _reflow(list(lines))


def test_growing_table_renders_are_prefix_stable():
    # The block flips prose -> pre once a second aligned row arrives. The single-row
    # render must remain a prefix of the multi-row render so dedup never splits it.
    frames = [
        ["Harness     Status"],
        ["Harness     Status", "claude_code OK"],
        ["Harness     Status", "claude_code OK", "cursor      ready"],
    ]
    rendered = [_reflow(f) for f in frames]
    for earlier, later in zip(rendered, rendered[1:]):
        assert later.startswith(earlier), f"{earlier!r} is not a prefix of {later!r}"


def test_streaming_accumulator_dedup_stable_under_growth():
    # Feed a claude_code pane whose assistant block grows line by line; the committed
    # segment count must never shrink (dedup keeps it one logical block).
    pane = [
        "● Here is the plan in detail and it spans",
        "  several wrapped lines of prose that the",
        "  harness rendered across the pane width.",
    ]
    frames = ["\n".join(pane[: n + 1]) for n in range(len(pane))]
    acc = transcripts.TranscriptAccumulator(harness="claude_code")
    counts = []
    for frame in frames:
        acc.feed(frame)
        counts.append(len(acc.to_dict()["segments"]))
    assert counts == sorted(counts), f"segment count regressed across frames: {counts}"
    # Re-feeding the last frame is idempotent.
    before = acc.to_dict()
    acc.feed(frames[-1])
    assert acc.to_dict() == before
