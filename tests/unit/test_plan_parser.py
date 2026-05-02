from __future__ import annotations

from datetime import datetime

import pytest

from murder.plans.parser import parse, render
from murder.plans.schema import Plan, PlanStatus


def test_parse_valid_plan_markdown() -> None:
    plan = parse(
        """---
name: launch
status: accepted
created_at: '2026-05-02T12:00:00'
related_tickets:
- t001
---
# Launch
"""
    )

    assert plan.name == "launch"
    assert plan.status == PlanStatus.ACCEPTED
    assert plan.related_tickets == ["t001"]
    assert plan.body == "# Launch\n"


def test_parse_invalid_frontmatter() -> None:
    with pytest.raises(ValueError, match="frontmatter"):
        parse("name: nope\n")


def test_render_round_trips() -> None:
    original = Plan(
        name="sync",
        status=PlanStatus.DRAFT,
        created_at=datetime(2026, 5, 2, 12, 0, 0),
        related_tickets=["t001", "t002"],
        body="## Plan\n\nDo it.\n",
    )

    parsed = parse(render(original))

    assert parsed.name == original.name
    assert parsed.status == original.status
    assert parsed.related_tickets == original.related_tickets
    assert parsed.body == original.body
