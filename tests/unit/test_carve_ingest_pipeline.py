"""Planner -> ticket carve/ingest pipeline.

Covers the two-point break found in dogfooding:
  1. a frontmatter-less ``tickets/<id>.md`` must ingest to a ``planned`` row;
  2. the planner's YAML carve form must be detectable and apply -> ``ready``,
     even when the row does not yet exist (upsert), and idempotently.
"""

from __future__ import annotations

from pathlib import Path

from murder.state.persistence import tickets as dbmod
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import ticket_md
from murder.work.tickets import carve
from murder.work.tickets.carve_scan import detect_carve_forms
from murder.work.tickets.parser import parse_ticket
from murder.work.tickets.status import TicketStatus
from murder.work.tickets.sync import TicketSync


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


# --------------------------------------------------------------------------- #
# 1. Frontmatter-less ingest -> planned
# --------------------------------------------------------------------------- #

PLANNER_MD = """## Plan

Implement the widget. Touch widget.py and tests.

## Working notes

(none)
"""


def test_frontmatterless_md_parses_without_error() -> None:
    parsed = parse_ticket(PLANNER_MD, default_title="t014")
    assert parsed.parse_error is None
    assert parsed.title == "t014"
    assert parsed.harness is None
    assert parsed.model is None


def test_frontmatterless_md_ingests_to_planned_row(repo_root: Path) -> None:
    conn = _conn(repo_root)
    path = ticket_md(repo_root, "t014")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PLANNER_MD, encoding="utf-8")

    err = TicketSync(repo_root, conn).reconcile_path(path)

    assert err is None
    row = dbmod.get_ticket(conn, "t014")
    assert row is not None
    assert row["status"] == TicketStatus.PLANNED.value


def test_malformed_frontmatter_still_errors(repo_root: Path) -> None:
    # An *opened-but-unclosed* frontmatter block is still a parse error.
    parsed = parse_ticket("---\ntitle: x\nno closing delimiter\n", default_title="t099")
    assert parsed.parse_error is not None


# --------------------------------------------------------------------------- #
# 2. Carve-form detection
# --------------------------------------------------------------------------- #

CARVE_PANE = """\
some planner chatter

```yaml
id: t014
title: Build the widget
write_set:
  - widget.py
deps: []
harness_override: codex
checklist:
  - widget renders
  - tests pass
```

trailing pane noise
"""


def test_detect_carve_form_from_pane() -> None:
    forms = detect_carve_forms(CARVE_PANE)
    assert len(forms) == 1
    assert forms[0]["id"] == "t014"
    assert forms[0]["title"] == "Build the widget"
    assert forms[0]["harness_override"] == "codex"


def test_detect_ignores_idless_or_nonmapping_blocks() -> None:
    pane = "```yaml\n- just\n- a\n- list\n```\n```yaml\ntitle: no id here\n```\n"
    assert detect_carve_forms(pane) == []


# A realistic claude_code/opus planner pane: the model narrates the carving
# step and emits the YAML form as BARE INDENTED text (no ```yaml fence). This is
# the shape that left tickets stuck in `planned` (H1).
CARVE_PANE_UNFENCED = """\
I've written the ticket file at .murder/tickets/t014.md. Here is the carving form:

    id: t014
    title: Build the widget
    write_set:
      - widget.py
      - tests/test_widget.py
    deps: []
    harness_override: codex
    checklist:
      - widget renders
      - tests pass

The .md file is the durable record; let me know if you want changes.
"""


def test_detect_carve_form_from_unfenced_indented_pane() -> None:
    forms = detect_carve_forms(CARVE_PANE_UNFENCED)
    assert len(forms) == 1
    assert forms[0]["id"] == "t014"
    assert forms[0]["title"] == "Build the widget"
    assert forms[0]["harness_override"] == "codex"
    assert forms[0]["write_set"] == ["widget.py", "tests/test_widget.py"]
    assert forms[0]["checklist"] == ["widget renders", "tests pass"]


def test_detect_handles_two_unfenced_forms_in_one_pane() -> None:
    pane = """\
First ticket:

    id: t001
    title: Alpha
    write_set:
      - a.py
    deps: []
    harness_override: codex
    checklist:
      - alpha works

Now the second:

    id: t002
    title: Beta
    write_set:
      - b.py
    deps: [t001]
    harness_override: codex
    checklist:
      - beta works
"""
    forms = detect_carve_forms(pane)
    assert [f["id"] for f in forms] == ["t001", "t002"]
    assert forms[1]["deps"] == ["t001"]


def test_detect_unfenced_ignores_bare_id_line_without_carve_shape() -> None:
    # A stray `id:` mention that is NOT a carve form (no title/write_set) is
    # ignored — the shape gate prevents false positives on noisy panes.
    pane = "Reference: see record with id: t999 for context.\nNothing else here.\n"
    assert detect_carve_forms(pane) == []


# --------------------------------------------------------------------------- #
# 3. Upsert when the row is absent + transition to ready
# --------------------------------------------------------------------------- #

CARVE_SPEC = {
    "id": "t014",
    "title": "Build the widget",
    "deps": [],
    "harness_override": "codex",
    "checklist": ["widget renders", "tests pass"],
}


def test_apply_carve_ready_inserts_then_readies_when_row_absent(repo_root: Path) -> None:
    conn = _conn(repo_root)
    assert dbmod.get_ticket(conn, "t014") is None

    carve.apply_carve_ready_spec(conn, "t014", dict(CARVE_SPEC))

    row = dbmod.get_ticket(conn, "t014")
    assert row is not None
    assert row["status"] == TicketStatus.READY.value
    assert row["title"] == "Build the widget"
    assert row["harness"] == "codex"
    assert [c.text for c in row.checklist] == ["widget renders", "tests pass"]


def test_apply_carve_ready_transitions_existing_planned_row(repo_root: Path) -> None:
    conn = _conn(repo_root)
    path = ticket_md(repo_root, "t014")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PLANNER_MD, encoding="utf-8")
    TicketSync(repo_root, conn).reconcile_path(path)
    assert dbmod.get_ticket(conn, "t014")["status"] == TicketStatus.PLANNED.value

    carve.apply_carve_ready_spec(conn, "t014", dict(CARVE_SPEC))

    assert dbmod.get_ticket(conn, "t014")["status"] == TicketStatus.READY.value


# --------------------------------------------------------------------------- #
# 4. Idempotency
# --------------------------------------------------------------------------- #


def test_apply_carve_ready_is_idempotent(repo_root: Path) -> None:
    conn = _conn(repo_root)
    prev1 = carve.apply_carve_ready_spec(conn, "t014", dict(CARVE_SPEC))
    prev2 = carve.apply_carve_ready_spec(conn, "t014", dict(CARVE_SPEC))

    assert prev1 == TicketStatus.PLANNED
    # Second apply is a safe no-op: already ready, no InvalidTransition raised.
    assert prev2 == TicketStatus.READY
    assert dbmod.get_ticket(conn, "t014")["status"] == TicketStatus.READY.value
