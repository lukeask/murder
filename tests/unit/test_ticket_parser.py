"""Ticket .md (prose-only, no frontmatter per D9) round-trips."""

from __future__ import annotations

import pytest


def test_parse_three_sections() -> None:
    # TODO(M4): feed a sample with all three sections; assert keys + bodies.
    pytest.skip("M4 stub")


def test_parse_missing_sections_returns_empty_strings() -> None:
    # TODO(M4): only ## Plan present; working_notes/sentinel_notes = ''.
    pytest.skip("M4 stub")


def test_render_round_trips() -> None:
    # TODO(M4): render(parse(text)) yields normalized but equivalent.
    pytest.skip("M4 stub")


def test_append_section_creates_missing() -> None:
    # TODO(M4): append to a file with only ## Plan; assert ## Working notes appears.
    pytest.skip("M4 stub")
