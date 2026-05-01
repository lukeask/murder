"""D6 protocol parsing: >>> CHECK / ASK / NOTE / DONE."""

from __future__ import annotations

import pytest

from murder.harnesses.base import (
    ASK_RE,
    CHECK_RE,
    DONE_RE,
    NOTE_RE,
    HarnessAdapter,
)


def test_check_regex_matches_simple_line() -> None:
    text = "blah\n>>> CHECK: implement Bar.parse\nblah"
    matches = [m.group("body").strip() for m in CHECK_RE.finditer(text)]
    assert matches == ["implement Bar.parse"]


def test_ask_regex_captures_multiline_body() -> None:
    text = ">>> ASK: should we use the new validator helper\nor write our own?\n>>> CHECK: x"
    m = ASK_RE.search(text)
    assert m is not None
    body = m.group("body").strip()
    # Both lines of the question should be present (multiline body).
    assert "validator helper" in body
    assert "write our own" in body


def test_done_regex_simple() -> None:
    assert DONE_RE.search("blah\n>>> DONE\n") is not None
    assert DONE_RE.search("not done") is None


def test_note_regex_multiline() -> None:
    text = ">>> NOTE: switched to dataclass for X\nbecause Y was awkward.\n"
    m = NOTE_RE.search(text)
    assert m is not None
    assert "switched to dataclass" in m.group("body")
