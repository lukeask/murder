"""DispatchStore — poll-fed store for ticket dispatch state.

Holds the ticket list for the header counts and the ticket grid.  ``as_of``
is excluded from the store snapshot so identical content on successive polls
does not trigger a spurious notify (same contract as DocumentStoreSnapshot).
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.service.client_api import DispatchSnapshot, TicketSummary
from murder.app.tui.stores.base import BaseStore


@dataclass(frozen=True, slots=True)
class DispatchStoreSnapshot:
    """Immutable snapshot emitted by DispatchStore.

    ``as_of`` from the server snapshot is deliberately excluded — it advances
    on every poll even when content is unchanged.
    """

    tickets: tuple[TicketSummary, ...]
    invalidation_key: str


class DispatchStore(BaseStore[DispatchStoreSnapshot]):
    def __init__(self) -> None:
        super().__init__(DispatchStoreSnapshot(tickets=(), invalidation_key=""))

    def ingest_snapshot(self, snapshot: DispatchSnapshot) -> None:
        """Called by the poll tick; notifies subscribers only when content changed."""
        self._set(
            DispatchStoreSnapshot(
                tickets=snapshot.tickets,
                invalidation_key=snapshot.invalidation_key,
            )
        )
