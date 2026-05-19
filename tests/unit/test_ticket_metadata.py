from __future__ import annotations

import pytest

from murder.bus import TicketStatus
from murder.tickets.sidecar import (
    TicketMetadata,
    TicketMetadataError,
    ensure_file_authored_status,
    parse_ticket_metadata,
    render_ticket_metadata,
    ticket_metadata_hash,
)


def test_parse_and_render_round_trip() -> None:
    text = """
id: t014
title: TUI quick-capture note overlay
wave: 3
status: ready
harness: cursor
model: Composer 2
deps:
  - t013
skills: []
write_set:
  - murder/tui/app.py
checklist:
  - ctrl+n opens overlay
schedule_at: null
"""
    meta = parse_ticket_metadata(text, expected_id="t014")
    rendered = render_ticket_metadata(meta)
    reparsed = parse_ticket_metadata(rendered, expected_id="t014")

    assert reparsed == meta
    assert "harness_override" not in rendered
    assert "harness: cursor" in rendered


def test_parse_uses_legacy_harness_override_alias() -> None:
    meta = parse_ticket_metadata(
        """
id: t001
title: Example
wave: 1
status: planned
harness_override: codex
deps: []
skills: []
write_set: []
checklist: []
schedule_at: null
""",
        expected_id="t001",
    )
    assert meta.harness == "codex"


def test_invalid_id_mismatch() -> None:
    with pytest.raises(TicketMetadataError, match="does not match expected id"):
        parse_ticket_metadata("id: t002\ntitle: x\nwave: 1\nstatus: planned\n", expected_id="t001")


def test_invalid_status_rejected() -> None:
    with pytest.raises(TicketMetadataError, match="invalid ticket status"):
        parse_ticket_metadata("id: t001\ntitle: x\nwave: 1\nstatus: queued\n")


def test_invalid_list_field_rejected() -> None:
    with pytest.raises(TicketMetadataError, match="deps must be a list of strings"):
        parse_ticket_metadata("id: t001\ntitle: x\nwave: 1\nstatus: planned\ndeps: [t000, 5]\n")


@pytest.mark.parametrize("entry", ["/abs/path.py", "../escape.py", "x/../../y.py"])
def test_invalid_write_set_entry_rejected(entry: str) -> None:
    with pytest.raises(TicketMetadataError, match="write_set entry"):
        parse_ticket_metadata(
            f"id: t001\ntitle: x\nwave: 1\nstatus: planned\nwrite_set:\n  - {entry}\n"
        )


def test_invalid_schedule_at_rejected() -> None:
    with pytest.raises(TicketMetadataError, match="valid ISO timestamp"):
        parse_ticket_metadata(
            "id: t001\ntitle: x\nwave: 1\nstatus: planned\nschedule_at: not-a-date\n"
        )


def test_file_authored_status_helper() -> None:
    ensure_file_authored_status(TicketStatus.PLANNED)
    ensure_file_authored_status(TicketStatus.READY)
    with pytest.raises(TicketMetadataError, match="file-authored status"):
        ensure_file_authored_status(TicketStatus.DONE)


def test_metadata_hash_stable() -> None:
    meta = TicketMetadata(id="t001", title="x", wave=1, status=TicketStatus.PLANNED)
    assert ticket_metadata_hash(meta) == ticket_metadata_hash(meta)
