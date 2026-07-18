"""Per-snapshot cross-language field-shape goldens for the read-reply DTOs (A-D2 / B-D5).

The Ink TUI declares a TypeScript interface per read-reply DTO that *hand-mirrors* a
Python dataclass in ``murder/app/service/client_api.py`` (``CrowSessionSummary``,
``PlanSummary``, ``NoteSummary``, ``ReportSummary``, ``TicketDetailSnapshot``, plus the
schedule snapshot's ``ScheduleTicketRow`` / ``UsageGaugeSummary``). There is no schema
validation at that seam, so a field rename or type change on either side drifts silently —
the plans-panel blank-out (sweep Finding 4) was exactly this class of bug.

This file is the **Python anchor** for a committed canonical JSON golden per DTO, following
the same pattern as the ``conversation.block`` golden (F11 H3,
``tests/unit/test_conversation_block_golden.py`` +
``inktui/test/store/conversations/conversationBlockContract.test.ts``):

  - The Python side (here) constructs the REAL dataclass with representative values and
    asserts that the REAL producer serializer — ``dto_to_wire`` wrapped in the
    ``host._value`` read envelope ``{"ok": True, "value": ...}`` — still equals the
    committed golden. ``dto_to_wire`` is the genuine wire path: enum→str, datetime→isoformat
    string, tuple→array. A field rename/type change on the Python side fails THIS test.
  - The Ink side (``inktui/test/store/dtoGoldens.contract.test.ts``) replays each golden
    through ``FakeBusClient`` (which live-wraps the read in the SAME ``{ok, value}`` envelope,
    A-D3) and asserts its slice consumer accepts the golden and projects the fields it reads.
    If an Ink consumer starts reading a key the producer doesn't emit, the Ink test fails.

The golden shape is the FULL post-envelope, post-serialization wire shape (``{ok, value}``),
so it doubles as the honesty check for ``FakeBusClient``'s live-wrap.

This complements — does NOT duplicate — Rook's ``tests/unit/test_protocol_agreement.py``
(commit 317ff16), which checks PROTOCOL_VERSION / Entity set / BusEvent names. That is the
protocol layer; this is the per-DTO field-shape layer.

Regenerate after an *intentional* shape change with::

    REGEN_GOLDEN=1 python -m pytest tests/unit/test_read_reply_dto_goldens.py -q

then update the matching Ink contract test if a key the consumer reads changed.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from murder.app.service.client_api import (
    ChecklistItem,
    CrowSessionSummary,
    CrowSnapshot,
    NoteSummary,
    NotesSnapshot,
    PlanSummary,
    PlansSnapshot,
    ReportSummary,
    ReportsSnapshot,
    ScheduleSnapshot,
    ScheduleTicketRow,
    SchedulerDecisionSummary,
    TicketDetailSnapshot,
    UsageGaugeSummary,
    dto_to_wire,
)
from murder.work.tickets.status import TicketStatus

# Goldens live under the Ink test tree so the TS contract test can import them directly.
GOLDEN_DIR = (
    Path(__file__).parent.parent.parent / "inktui" / "test" / "fixtures" / "read-reply-goldens"
)

# Fixed deterministic timestamps (dto_to_wire renders datetime via .isoformat()).
AS_OF = datetime(2026, 6, 9, 0, 0, 0)
TS = datetime(2026, 6, 9, 12, 30, 0)


def _wire_envelope(dto: Any) -> dict[str, Any]:
    """Reproduce the REAL read-reply wire shape: ``host._value(dto)`` →
    ``{"ok": True, "value": dto_to_wire(dto)}`` (host.py:141-142)."""
    return {"ok": True, "value": dto_to_wire(dto)}


# ---------------------------------------------------------------------------
# Representative real dataclass instances — one per read-reply DTO. Field values are
# chosen to exercise every serialization branch the consumer relies on: enum→str
# (TicketStatus), datetime→isoformat (as_of/updated_at/last_seen), tuple→array
# (checklist/deps/sessions/plans/pending_dep_ids), and the None/non-None nullable columns.
# ---------------------------------------------------------------------------


def _ticket_detail_dto() -> TicketDetailSnapshot:
    return TicketDetailSnapshot(
        id="T-101",
        title="Wire the detail pane",
        status=TicketStatus.IN_PROGRESS,
        body="# Body\n\nDo the thing.\n\n# Checklist\n- [x] read files\n- [ ] write code\n",
        checklist=(
            ChecklistItem(text="read files", done=True),
            ChecklistItem(text="write code", done=False),
        ),
        deps=("T-100", "T-099"),
        harness="cc",
        model="opus",
        worktree="/wt/t-101",
        schedule_at=None,
        plan_md="# Body\n\nDo the thing.\n",
        working_notes_md="started reading\n",
        as_of=AS_OF,
        invalidation_key="ticket_detail:T-101",
    )


def _crow_snapshot_dto() -> CrowSnapshot:
    return CrowSnapshot(
        sessions=(
            CrowSessionSummary(
                agent_id="crow-7",
                role="crow",
                ticket_id="T-101",
                ticket_title="Wire the detail pane",
                status="running",
                session_name="crow-7-sess",
                harness="cc",
                last_seen=TS,
                started_at=AS_OF,
                ticket_status="in_progress",
                worktree_path="/wt/t-101",
                model="opus",
                open_escalations=2,
                max_severity=3,
                session_id="0198b156-2dd3-70a9-bc79-fca001dc8801",
            ),
            # A second row exercising the nullable columns as None (default fields too).
            CrowSessionSummary(
                agent_id="planner-1",
                role="planner",
                ticket_id=None,
                ticket_title=None,
                status="idle",
                session_name=None,
                harness=None,
                last_seen=None,
                started_at=None,
                ticket_status=None,
            ),
        ),
        as_of=AS_OF,
        invalidation_key="crows",
    )


def _plans_snapshot_dto() -> PlansSnapshot:
    return PlansSnapshot(
        plans=(
            PlanSummary(
                name="root-plan",
                status="active",
                revision_count=3,
                sync_state="synced",
                parent=None,
                updated_at=TS,
                char_count=4096,
            ),
            PlanSummary(
                name="child-plan",
                status="draft",
                revision_count=1,
                sync_state="dirty",
                parent="root-plan",
                updated_at=AS_OF,
                char_count=512,
            ),
        ),
        as_of=AS_OF,
        invalidation_key="plans",
    )


def _notes_snapshot_dto() -> NotesSnapshot:
    return NotesSnapshot(
        notes=(NoteSummary(name="design-notes", char_count=2048, updated_at=TS),),
        as_of=AS_OF,
        invalidation_key="notes",
    )


def _reports_snapshot_dto() -> ReportsSnapshot:
    return ReportsSnapshot(
        reports=(ReportSummary(name="review-report", char_count=1024, updated_at=TS),),
        as_of=AS_OF,
        invalidation_key="reports",
    )


def _schedule_snapshot_dto() -> ScheduleSnapshot:
    row = ScheduleTicketRow(
        id="T-101",
        title="Wire the detail pane",
        status="in_progress",
        last_update_at=TS,
        last_update_label="2m ago",
        schedule_at=None,
        harness="cc",
        model="opus",
        metadata_sync_state="synced",
        metadata_parse_error=None,
        metadata_conflict_reason=None,
        pending_dep_ids=("T-100",),
    )
    gauge = UsageGaugeSummary(
        harness="cc",
        window_key="5h",
        pct=42.5,
        t_until_reset_minutes=120.0,
        t_period_minutes=300.0,
        fetched_at="2026-06-09T11:58:00",
    )
    return ScheduleSnapshot(
        scheduler_mode="auto",
        mode_rationale="capacity available",
        active_tickets=(row,),
        recent_done_tickets=(),
        archived_tickets=(),
        scheduler_decisions=(
            SchedulerDecisionSummary(
                harness="cc", decision=1, rationale="kick", kicked_ticket_id="T-101"
            ),
        ),
        usage_gauges=(gauge,),
        calendar_harnesses=("cc",),
        running_agents=(),
        scheduled_tickets=(),
        as_of=AS_OF,
        invalidation_key="schedule",
    )


# (golden filename stem, RPC method the Ink consumer calls, dataclass factory)
_CASES: list[tuple[str, str, Any]] = [
    ("ticket-detail", "state.ticket_detail", _ticket_detail_dto),
    ("crow-snapshot", "state.crow_snapshot", _crow_snapshot_dto),
    ("plans-snapshot", "state.plans_snapshot", _plans_snapshot_dto),
    ("notes-snapshot", "state.notes_snapshot", _notes_snapshot_dto),
    ("reports-snapshot", "state.reports_snapshot", _reports_snapshot_dto),
    ("schedule-snapshot", "state.schedule_snapshot", _schedule_snapshot_dto),
]


@pytest.mark.parametrize("stem, method, factory", _CASES, ids=[c[0] for c in _CASES])
def test_read_reply_dto_matches_golden(stem: str, method: str, factory: Any) -> None:
    """The real producer serializer (dto_to_wire + read envelope) still equals the
    committed cross-language golden. Fails if any wire key/type drifts on the Python side."""
    wire = _wire_envelope(factory())
    golden_path = GOLDEN_DIR / f"{stem}.json"

    if os.environ.get("REGEN_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")

    assert golden_path.exists(), (
        f"golden missing at {golden_path}; regenerate with REGEN_GOLDEN=1"
    )
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert wire == golden, (
        f"{method} read-reply wire shape drifted from the committed golden. "
        "If this change is intentional, regenerate with REGEN_GOLDEN=1 and update the "
        "matching Ink contract test."
    )


def test_goldens_are_read_envelopes() -> None:
    """Coverage guard: every golden is the post-envelope wire shape ({ok, value}),
    so it doubles as FakeBusClient's live-wrap honesty check (A-D3)."""
    for stem, _method, factory in _CASES:
        wire = _wire_envelope(factory())
        assert wire["ok"] is True
        assert "value" in wire
        # The committed golden must already be on disk (run REGEN_GOLDEN=1 once).
        assert (GOLDEN_DIR / f"{stem}.json").exists()
