"""Cursor grammar fail-soft path: no ANSI colour capture (`-e` missing).

Cursor's role detection rides entirely on ANSI background colour — user-input
blocks and the live composer are tagged in ``preprocess_frame`` with control-char
sentinels derived from captured SGR backgrounds. When a frame is captured WITHOUT
``-e`` (or Cursor changes its RGBs), no marks are injected and role recovery
degrades to the murder-owned anchor fallback. These tests pin that degraded path:
it must never crash, must still extract assistant prose, and must emit a single
loud warning so a future RGB change is caught rather than silently mis-rolled.

See plan-codex-cursor-hardening.md "ANSI-pin fail-soft" acceptance.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import murder.llm.harnesses.transcripts.grammar.cursor as cursor_grammar
from murder.llm.harnesses.transcripts.grammar.cursor import (
    _CHROME_MARK,
    _USER_MARK,
    parse_lines,
    preprocess_frame,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harness_panes"
# Match SGR colour sequences only (\x1b[...m) — the same shape strip_ansi removes.
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _stripped_cursor_idle() -> str:
    """Load the real cursor idle fixture and remove every ANSI colour code,
    simulating a capture taken without the tmux ``-e`` flag."""
    raw = (_FIXTURES / "cursor_idle.txt").read_text()
    return _ANSI_SGR_RE.sub("", raw)


def _reset_warn_flag() -> None:
    cursor_grammar._warned_no_marks = False


def test_stripped_frame_injects_no_marks() -> None:
    _reset_warn_flag()
    out = preprocess_frame(_stripped_cursor_idle())
    assert _USER_MARK not in out
    assert _CHROME_MARK not in out


def test_stripped_idle_frame_parses_without_crash() -> None:
    _reset_warn_flag()
    processed = preprocess_frame(_stripped_cursor_idle())
    # Full grammar over the degraded idle frame: must not raise. An idle pane is
    # pure chrome (placeholder + status line), so zero segments is correct — the
    # contract is "never crashes, never blank-explodes", not "always non-empty".
    segments = parse_lines(processed.splitlines())
    assert isinstance(segments, list)


def test_stripped_frame_with_prose_still_extracts_assistant() -> None:
    _reset_warn_flag()
    # Assistant prose splitting is colour-independent (blank-line blocks), so even
    # with NO marks the degraded path must still recover assistant text. Indented
    # lines (leading space) are kept; bare lines are treated as chrome.
    frame = (
        "  Cursor Agent\n"
        "\n"
        "  Here is the analysis of the bug you reported.\n"
        "  It is a fixed-sleep race in the picker.\n"
        "\n"
        "  Composer 2.5                                  Auto-run\n"
    )
    processed = preprocess_frame(frame)
    assert _USER_MARK not in processed and _CHROME_MARK not in processed
    segments = parse_lines(processed.splitlines())
    texts = " ".join(s.get("text", "") for s in segments if s.get("type") == "assistant")
    assert "analysis of the bug" in texts


def test_warn_flag_set_on_first_markless_frame() -> None:
    _reset_warn_flag()
    assert cursor_grammar._warned_no_marks is False
    preprocess_frame(_stripped_cursor_idle())
    assert cursor_grammar._warned_no_marks is True


def test_warning_emitted_once_only(caplog) -> None:
    _reset_warn_flag()
    stripped = _stripped_cursor_idle()
    with caplog.at_level(logging.WARNING, logger=cursor_grammar.__name__):
        preprocess_frame(stripped)
        preprocess_frame(stripped)
    warnings = [r for r in caplog.records if "no ANSI colour marks" in r.getMessage()]
    assert len(warnings) == 1, "fail-soft warning must fire once, not per-frame"


def test_no_warning_when_marks_present() -> None:
    _reset_warn_flag()
    # A frame that DOES carry the user-input background must not trip the sentinel.
    frame = "\x1b[48;2;36;36;40m  hello from the user\x1b[0m\nassistant reply"
    out = preprocess_frame(frame)
    assert _USER_MARK in out
    assert cursor_grammar._warned_no_marks is False


def test_no_warning_on_empty_frame() -> None:
    _reset_warn_flag()
    # An all-whitespace frame has no content, so the missing-marks sentinel must
    # stay quiet — otherwise startup blank frames would spam the warning.
    preprocess_frame("\n   \n\n")
    assert cursor_grammar._warned_no_marks is False
