"""Crow health classification for the tail-wall borders.

Health is a client-side fact derived from existing DB snapshots
(`agents.status`, open `escalations`), per VISION.md §5. The classifier
is intentionally pure so it can be unit-tested without spinning up
Textual or sqlite.

When the bus eventually streams `crow_health` events (VISION §7.1), the
input shape stays the same — only the data source moves.
"""

from __future__ import annotations

from enum import Enum


class Health(str, Enum):
    NEUTRAL = "neutral"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


_RED_STATUSES = frozenset({"escalating", "blocked", "failed", "dead"})
_GREEN_STATUSES = frozenset({"running", "idle"})
_DONE_STATUSES = frozenset({"done"})

# Severity 1 is informational; severity ≥ 2 is "needs human attention" per
# the protocol enum and turns a crow's border red even with zero open rows
# (defensive — caller's count and max severity should align in practice).
_RED_SEVERITY_THRESHOLD = 2


def classify(
    *,
    status: str | None,
    open_escalations: int = 0,
    max_severity: int = 0,
    stuck: bool = False,
) -> Health:
    """Pick the border color for one crow.

    Precedence (first match wins):
      RED    — an open escalation linked to this crow's ticket, or the
               agent itself is in a red status.
      YELLOW — heartbeat says stuck-but-alive (caller decides; the
               classifier just respects the flag).
      GREEN  — agent is running or idle.
      NEUTRAL— done, or any state we don't have a positive read on.

    `max_severity` lets a sev-1 escalation downgrade to red even if the
    open count is zero by row but >0 by query semantics; callers that
    don't track severities can pass 0.
    """
    if open_escalations > 0 or max_severity >= _RED_SEVERITY_THRESHOLD:
        return Health.RED
    norm = (status or "").lower()
    if norm in _RED_STATUSES:
        return Health.RED
    if stuck:
        return Health.YELLOW
    if norm in _GREEN_STATUSES:
        return Health.GREEN
    if norm in _DONE_STATUSES:
        return Health.NEUTRAL
    return Health.NEUTRAL


HEALTH_BORDER_COLOR: dict[Health, str] = {
    Health.RED: "red",
    Health.YELLOW: "yellow",
    Health.GREEN: "green",
    Health.NEUTRAL: "$border",
}
