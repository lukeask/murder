"""EscalationsStore — poll-fed store for escalation strip state.

Holds active and history escalations.  ``as_of`` is excluded so identical
content on successive polls does not trigger a spurious notify.
"""

from __future__ import annotations

from dataclasses import dataclass

from murder.app.service.client_api import EscalationSummary, EscalationsSnapshot
from murder.app.tui.stores.base import BaseStore


@dataclass(frozen=True, slots=True)
class EscalationsStoreSnapshot:
    """Immutable snapshot emitted by EscalationsStore.

    ``as_of`` from the server snapshot is deliberately excluded.
    """

    active: tuple[EscalationSummary, ...]
    history: tuple[EscalationSummary, ...]
    invalidation_key: str


class EscalationsStore(BaseStore[EscalationsStoreSnapshot]):
    def __init__(self) -> None:
        super().__init__(
            EscalationsStoreSnapshot(active=(), history=(), invalidation_key="")
        )

    def ingest_snapshot(self, snapshot: EscalationsSnapshot) -> None:
        """Called by the poll tick; notifies subscribers only when content changed."""
        self._set(
            EscalationsStoreSnapshot(
                active=snapshot.active,
                history=snapshot.history,
                invalidation_key=snapshot.invalidation_key,
            )
        )
