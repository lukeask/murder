"""Chat-target cycling helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.app.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.app.tui.chat_target_cycle import (
    ChatTarget,
    crows_chat_targets,
    cycle_chat_target,
    planning_chat_targets,
)
from murder.app.tui.crows_view import CrowEntry, Health


def _session(**kwargs: object) -> CrowSessionSummary:
    defaults = dict(
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        ticket_title="Fix thing",
        status="running",
        session_name="murder_demo_crow_t001",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


def _crow_entry(agent_id: str, *, ticket_id: str = "t001") -> CrowEntry:
    return CrowEntry(
        agent_id=agent_id,
        ticket_id=ticket_id,
        ticket_title="Fix thing",
        harness="cursor",
        status="running",
        session=f"session-{agent_id}",
        health=Health.GREEN,
    )


def test_planning_chat_targets_collaborator_then_live_planners() -> None:
    snap = CrowSnapshot(
        sessions=(
            _session(agent_id="planner-beta", role="planner"),
            _session(agent_id="planner-alpha", role="planner"),
            _session(agent_id="planner-done", role="planner", status="done"),
        ),
        as_of=datetime.now(timezone.utc),
        invalidation_key="k",
    )
    targets = planning_chat_targets(snap)
    assert [t.label for t in targets] == [
        "collaborator",
        "planner: alpha",
        "planner: beta",
    ]


def test_crows_chat_targets_follows_wall_order() -> None:
    entries = {
        "crow-a": _crow_entry("crow-a", ticket_id="t002"),
        "codex-rogue-tailwall": CrowEntry(
            agent_id="codex-rogue-tailwall",
            ticket_id="",
            ticket_title="tailwall",
            harness="codex",
            status="running",
            session="murder_repo_crow_codex_rogue_tailwall",
            health=Health.GREEN,
            model="gpt-5.4",
        ),
    }
    targets = crows_chat_targets(["codex-rogue-tailwall", "crow-a"], entries)
    assert [t.agent_id for t in targets] == ["codex-rogue-tailwall", "crow-a"]
    assert targets[0].label == "tailwall codex gpt-5.4 rogue"


def test_cycle_chat_target_wraps_forward_and_backward() -> None:
    targets = [
        ChatTarget(None, "collaborator"),
        ChatTarget("planner-a", "planner: a"),
        ChatTarget("planner-b", "planner: b"),
    ]
    assert cycle_chat_target(targets, None, 1) == targets[1]
    assert cycle_chat_target(targets, "planner-b", 1) == targets[0]
    assert cycle_chat_target(targets, "planner-a", -1) == targets[0]
    assert cycle_chat_target(targets, None, -1) == targets[2]


def test_cycle_chat_target_noop_for_single_target() -> None:
    targets = [ChatTarget(None, "collaborator")]
    assert cycle_chat_target(targets, None, 1) is None


def test_cycle_chat_target_unknown_current_starts_from_first() -> None:
    targets = [
        ChatTarget(None, "collaborator"),
        ChatTarget("planner-a", "planner: a"),
    ]
    assert cycle_chat_target(targets, "planner-missing", 1) == targets[1]
