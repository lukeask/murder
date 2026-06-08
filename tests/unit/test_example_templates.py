"""C13 — copyable example templates + restore hook."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

import pytest

from murder.app.service.filesystem_sync import FilesystemSyncSupervisor
from murder.state.persistence.schema import init_db
from murder.state.storage.paths import murder_dir, plans_dir, tickets_dir
from murder.work.examples import EXAMPLE_TEMPLATES, example_path, seed_examples
from murder.work.plans import parser as plan_parser
from murder.work.tickets.parser import parse_ticket
from murder.work.tickets.sync import _TICKET_ID_RE


def _template_text(filename: str) -> str:
    return (
        resources.files("murder.resources.templates")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def test_ticket_template_parses_cleanly() -> None:
    parsed = parse_ticket(_template_text("example_ticket.md"))
    assert parsed.parse_error is None
    assert parsed.title
    assert parsed.harness
    assert parsed.model
    # The incremental-check instruction lives under # Checklist as prose, but the
    # box items must still parse.
    assert parsed.checklist
    assert all(not item.done for item in parsed.checklist)


def test_plan_template_parses_cleanly() -> None:
    plan = plan_parser.parse(_template_text("example_plan.md"))
    assert plan.name
    assert plan.body


def test_example_stems_never_match_ticket_id() -> None:
    # The hiding mechanism: example stems have no digit, and they live at the
    # .murder/ top level — so neither sync worker (which globs only its subdir)
    # ingests them, and the ticket-id regex rejects them defensively.
    for filename in EXAMPLE_TEMPLATES:
        assert _TICKET_ID_RE.fullmatch(Path(filename).stem) is None


def test_seed_creates_missing_examples(tmp_path: Path) -> None:
    written = seed_examples(tmp_path)
    assert {p.name for p in written} == set(EXAMPLE_TEMPLATES)
    for filename in EXAMPLE_TEMPLATES:
        dest = example_path(tmp_path, filename)
        assert dest.exists()
        # Lives at .murder/ top level, not in tickets/ or plans/.
        assert dest.parent == murder_dir(tmp_path)
        assert dest.parent != tickets_dir(tmp_path)
        assert dest.parent != plans_dir(tmp_path)


def test_seed_restores_deleted_default(tmp_path: Path) -> None:
    seed_examples(tmp_path)
    target = example_path(tmp_path, "example_ticket.md")
    target.unlink()
    assert not target.exists()

    written = seed_examples(tmp_path)
    assert target in written
    assert target.exists()


def test_seed_is_idempotent_and_preserves_edits(tmp_path: Path) -> None:
    seed_examples(tmp_path)
    target = example_path(tmp_path, "example_plan.md")
    target.write_text("--- edited by user ---", encoding="utf-8")

    written = seed_examples(tmp_path)
    assert written == []  # nothing re-created
    assert target.read_text(encoding="utf-8") == "--- edited by user ---"


@pytest.mark.asyncio
async def test_supervisor_reconcile_restores_example(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    supervisor = FilesystemSyncSupervisor.attach(tmp_path, conn)

    await supervisor.reconcile_all()
    target = example_path(tmp_path, "example_ticket.md")
    assert target.exists()

    target.unlink()
    await supervisor.reconcile_all()
    assert target.exists()
