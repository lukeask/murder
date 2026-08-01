"""Internal grading trace events.

Short reason codes only — no chain-of-thought. Not part of recipient briefs.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GradingTraceEvent:
    """One structured grading event."""

    kind: str
    reason_code: str
    path: str = ""
    detail: str = ""
    proposal_index: int | None = None


@dataclass
class GradingTrace:
    """Mutable collector for grading / repair / expansion events."""

    events: list[GradingTraceEvent] = field(default_factory=list)

    def record(
        self,
        kind: str,
        reason_code: str,
        *,
        path: str = "",
        detail: str = "",
        proposal_index: int | None = None,
    ) -> None:
        self.events.append(
            GradingTraceEvent(
                kind=kind,
                reason_code=reason_code,
                path=path,
                detail=detail,
                proposal_index=proposal_index,
            )
        )


__all__ = [
    "GradingTrace",
    "GradingTraceEvent",
]
