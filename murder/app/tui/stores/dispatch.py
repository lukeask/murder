"""DispatchStore — poll-fed store for ticket dispatch state.

Holds the ticket list for the header counts and the ticket grid.  ``as_of``
is excluded from the store snapshot so identical content on successive polls
does not trigger a spurious notify (same contract as DocumentStoreSnapshot).

Derived fields (moved store-side for Phase 2 component library):
  - ``attention_counts``: per-status counts for the header attention segment
    (blocked/failed); computed once on ingest, not in each subscriber.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.service.client_api import DispatchSnapshot, TicketSummary
from murder.app.tui.stores.base import BaseStore

_ATTENTION_STATUSES = ("blocked", "failed")


@dataclass(frozen=True, slots=True)
class DispatchStoreSnapshot:
    """Immutable snapshot emitted by DispatchStore.

    ``as_of`` from the server snapshot is deliberately excluded — it advances
    on every poll even when content is unchanged.

    ``attention_counts`` is a derived field: per-status ticket counts for the
    statuses the header attention segment cares about (blocked, failed).
    """

    tickets: tuple[TicketSummary, ...]
    invalidation_key: str
    attention_counts: tuple[tuple[str, int], ...] = ()


def _compute_attention_counts(
    tickets: tuple[TicketSummary, ...],
) -> tuple[tuple[str, int], ...]:
    """Count tickets per attention-worthy status."""
    counts: dict[str, int] = {s: 0 for s in _ATTENTION_STATUSES}
    for ticket in tickets:
        # status may be a TicketStatus enum or a plain string.
        raw = ticket.status
        key = raw.value if hasattr(raw, "value") else str(raw)
        if key in counts:
            counts[key] += 1
    return tuple((s, counts[s]) for s in _ATTENTION_STATUSES)


class DispatchStore(BaseStore[DispatchStoreSnapshot]):
    def __init__(self) -> None:
        super().__init__(DispatchStoreSnapshot(tickets=(), invalidation_key=""))

    def ingest_snapshot(self, snapshot: DispatchSnapshot) -> None:
        """Called by the poll tick; notifies subscribers only when content changed."""
        self._set(
            DispatchStoreSnapshot(
                tickets=snapshot.tickets,
                invalidation_key=snapshot.invalidation_key,
                attention_counts=_compute_attention_counts(snapshot.tickets),
            )
        )
