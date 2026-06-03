"""Crow health classification for the tail-wall borders.

Health is a client-side fact derived from ``CrowSessionSummary.status`` and
``last_seen``, per VISION.md §5. The classifier is intentionally pure so it
can be unit-tested without spinning up Textual.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum


class Health(str, Enum):
    NEUTRAL = "neutral"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


_RED_STATUSES = frozenset({"escalating", "blocked", "failed", "dead"})
_GREEN_STATUSES = frozenset({"running", "idle"})
_DONE_STATUSES = frozenset({"done"})

STUCK_AFTER = timedelta(seconds=60)
"""Running/idle crows with no heartbeat newer than this are stuck-but-alive."""

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


def is_stuck(
    *,
    status: str | None,
    last_seen: datetime | None,
    now: datetime,
) -> bool:
    """True when a live crow's heartbeat is older than :data:`STUCK_AFTER`."""
    norm = (status or "").lower()
    if norm not in _GREEN_STATUSES:
        return False
    if last_seen is None:
        return False
    seen = _as_utc(last_seen)
    return now - seen > STUCK_AFTER


def health_for_session(
    *,
    status: str,
    last_seen: datetime | None,
    now: datetime | None = None,
) -> Health:
    """Border color for one :class:`~murder.service.client_api.CrowSessionSummary`."""
    now_utc = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    return classify(
        status=status,
        stuck=is_stuck(status=status, last_seen=last_seen, now=now_utc),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


HEALTH_BORDER_COLOR: dict[Health, str] = {
    Health.RED: "$crow-health-red",
    Health.YELLOW: "$crow-health-yellow",
    Health.GREEN: "$crow-health-green",
    Health.NEUTRAL: "$crow-health-neutral",
}
