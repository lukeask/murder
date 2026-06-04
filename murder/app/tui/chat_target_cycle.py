"""Chat-target cycling while the chat input is focused."""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.service.client_api import CrowSnapshot
from murder.app.tui.crows_view import CrowEntry, crow_title_label

_ACTIVE_AGENT_STATUSES = frozenset({"running", "idle"})


@dataclass(frozen=True, slots=True)
class ChatTarget:
    agent_id: str | None
    label: str


def planning_chat_targets(snapshot: CrowSnapshot | None) -> list[ChatTarget]:
    """Collaborator first, then live per-plan planning agents."""
    targets = [ChatTarget(None, "collaborator")]
    if snapshot is None:
        return targets
    planners = [
        session
        for session in snapshot.sessions
        if session.role == "planner" and session.status in _ACTIVE_AGENT_STATUSES
    ]
    planners.sort(key=lambda session: session.agent_id)
    for session in planners:
        plan_name = session.agent_id
        if plan_name.startswith("planner-"):
            plan_name = plan_name[len("planner-") :]
        targets.append(ChatTarget(session.agent_id, f"planner: {plan_name}"))
    return targets


def crows_chat_targets(
    wall_order: list[str],
    entries_by_id: dict[str, CrowEntry],
) -> list[ChatTarget]:
    """Crows with pane-visible tails, in tail-wall order."""
    targets: list[ChatTarget] = []
    for agent_id in wall_order:
        entry = entries_by_id.get(agent_id)
        if entry is None:
            continue
        targets.append(ChatTarget(agent_id, crow_title_label(entry)))
    return targets


def cycle_chat_target(
    targets: list[ChatTarget],
    current_agent_id: str | None,
    delta: int,
) -> ChatTarget | None:
    """Move ``delta`` steps through ``targets``, wrapping. No-op if fewer than two."""
    if len(targets) < 2 or delta == 0:
        return None
    try:
        idx = next(i for i, target in enumerate(targets) if target.agent_id == current_agent_id)
    except StopIteration:
        idx = 0
    next_idx = (idx + delta) % len(targets)
    if next_idx == idx:
        return None
    return targets[next_idx]
