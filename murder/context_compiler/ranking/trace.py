"""Internal ranking trace events (exclusions, scores).

Not part of recipient-facing models. Tests and Step 5 previews may read
these; ``CorpusProposal`` must not grow an exclusions field.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TraceEvent:
    """One structured ranking/shaping event."""

    kind: str
    reason_code: str
    path: str
    detail: str = ""
    score: float | None = None


@dataclass
class RankingTrace:
    """Mutable collector for exclusion and score events."""

    events: list[TraceEvent] = field(default_factory=list)

    def record(
        self,
        kind: str,
        reason_code: str,
        *,
        path: str = "",
        detail: str = "",
        score: float | None = None,
    ) -> None:
        self.events.append(
            TraceEvent(
                kind=kind,
                reason_code=reason_code,
                path=path,
                detail=detail,
                score=score,
            )
        )

    def exclusions(self) -> tuple[TraceEvent, ...]:
        return tuple(e for e in self.events if e.kind == "excluded")

    def scores(self) -> tuple[TraceEvent, ...]:
        return tuple(e for e in self.events if e.kind == "scored")


__all__ = [
    "RankingTrace",
    "TraceEvent",
]
