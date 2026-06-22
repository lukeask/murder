from __future__ import annotations

from murder.work.tickets.parser import parse_ticket
from murder.work.tickets.render import render_ticket_frontmatter


def test_parse_ticket_accepts_aliases_and_unknown_keys_as_extras() -> None:
    parsed = parse_ticket(
        """---
title: Alias ticket
dependencies: [t001, t002]
harness_override: codex
model: gpt-5.5
priority: high
---
# Body
"""
    )

    assert parsed.parse_error is None
    assert parsed.title == "Alias ticket"
    assert parsed.deps == ["t001", "t002"]
    assert parsed.harness == "codex"
    assert parsed.model == "gpt-5.5"
    assert parsed.worktree is None
    assert parsed.extras == {"priority": "high"}


def test_parse_ticket_reports_missing_required_fields_without_raising() -> None:
    parsed = parse_ticket(
        """---
deps: []
---
# Body
"""
    )

    assert parsed.title is None
    assert parsed.deps == []
    assert parsed.body == "# Body\n"
    assert parsed.parse_error is not None
    assert "title" in parsed.parse_error
    assert "harness" in parsed.parse_error
    assert "model" in parsed.parse_error


def test_parse_ticket_recovers_title_from_heading_when_frontmatterless() -> None:
    # Bug H4: `ticket.quick_create` writes a frontmatter-less `# {title}` file.
    # The reconcile re-parse must recover the typed title from the heading instead
    # of falling back to `default_title` (the id), which clobbered the DB title.
    parsed = parse_ticket(
        "# Brand new test ticket\n\n## Plan\n\n## Working Notes\n",
        default_title="t031",
    )

    assert parsed.parse_error is None
    assert parsed.title == "Brand new test ticket"


def test_parse_ticket_heading_fallback_skips_checklist_header() -> None:
    # The structural `# Checklist` header must not be mistaken for a title; with no
    # other heading, fall back to default_title.
    parsed = parse_ticket(
        "# Checklist\n[ ] do thing\n",
        default_title="t032",
    )

    assert parsed.title == "t032"


def test_parse_ticket_heading_fallback_not_applied_when_frontmatter_present() -> None:
    # A frontmatter ticket missing `title:` is a reportable error, NOT a heading
    # scrape — keep title None so the missing-field error stands.
    parsed = parse_ticket(
        """---
deps: []
---
# Heading
""",
    )

    assert parsed.title is None
    assert parsed.parse_error is not None
    assert "title" in parsed.parse_error


def test_parse_ticket_reports_malformed_yaml_without_raising() -> None:
    parsed = parse_ticket(
        """---
title: [unterminated
---
# Body
"""
    )

    assert parsed.parse_error is not None
    assert "invalid ticket frontmatter YAML" in parsed.parse_error
    assert parsed.body == "# Body\n"


def test_parse_ticket_checklist_reads_only_level_one_checklist_marker_lines() -> None:
    parsed = parse_ticket(
        """---
title: Checklist ticket
harness: cc
model: opus
deps: []
worktree:
---
# Intro
[ ] not in checklist

# Checklist
[ ] first item
[x] done item
- [ ] ignored markdown task
plain text ignored
## Nested heading ignored
[X] uppercase done
# Later
[ ] after next h1 ignored
"""
    )

    assert parsed.parse_error is None
    assert [(item.text, item.done) for item in parsed.checklist] == [
        ("first item", False),
        ("done item", True),
        ("uppercase done", True),
    ]


def test_ticket_parse_render_parse_round_trip_is_stable() -> None:
    source = """---
title: Round trip
deps: [t001]
harness: codex
model: gpt-5.5
worktree: feature-x
ignored: value
---
# Checklist
[ ] keep this
[x] and this
"""
    first = parse_ticket(source)
    rendered = render_ticket_frontmatter(first) + first.body
    second = parse_ticket(rendered)
    rendered_again = render_ticket_frontmatter(second) + second.body

    assert first.parse_error is None
    assert second.parse_error is None
    assert rendered_again == rendered
    assert second.extras == {}
    assert [(item.text, item.done) for item in second.checklist] == [
        ("keep this", False),
        ("and this", True),
    ]


def test_render_ticket_frontmatter_emits_exact_canonical_keys() -> None:
    parsed = parse_ticket(
        """---
title: Canonical
harness: cc
model: opus
extra: ignored
---
Body
"""
    )

    frontmatter = render_ticket_frontmatter(parsed)

    assert frontmatter == (
        "---\n"
        "title: Canonical\n"
        "deps: []\n"
        "harness: cc\n"
        "model: opus\n"
        "worktree: null\n"
        "parent: null\n"
        "---\n"
    )


def test_parse_ticket_reads_parent_as_scalar() -> None:
    parsed = parse_ticket(
        """---
title: Child
harness: cc
model: opus
parent: t003
---
# Body
"""
    )

    assert parsed.parse_error is None
    assert parsed.parent == "t003"
    # `parent` is canonical, not an extra.
    assert parsed.extras == {}


def test_parse_ticket_absent_parent_is_none() -> None:
    parsed = parse_ticket(
        """---
title: Top-level
harness: cc
model: opus
---
# Body
"""
    )

    assert parsed.parse_error is None
    assert parsed.parent is None


def test_render_parse_round_trip_preserves_parent() -> None:
    source = """---
title: Child ticket
deps: []
harness: codex
model: gpt-5.5
worktree: null
parent: t003
---
# Checklist
[ ] do thing
"""
    first = parse_ticket(source)
    rendered = render_ticket_frontmatter(first) + first.body
    second = parse_ticket(rendered)

    assert first.parent == "t003"
    assert second.parent == "t003"
    # Render is stable across a second pass (no parent drift).
    assert render_ticket_frontmatter(second) + second.body == rendered


def test_render_omits_parent_value_when_none() -> None:
    parsed = parse_ticket(
        """---
title: Top-level
harness: cc
model: opus
---
# Body
"""
    )

    frontmatter = render_ticket_frontmatter(parsed)

    # Matches the other optional scalars: the key is present with a `null` value
    # rather than carrying a stale id, and re-parses back to None.
    assert "parent: null\n" in frontmatter
    assert parse_ticket(frontmatter + parsed.body).parent is None
