"""History overlay I/O concern (mirrors ``note_ops`` for the history view).

The history feed is a read model over the durable user-message spine; the only
write path in v0 is *dismiss*, which records a terminal status in the
``history_status`` overlay table and republishes the history snapshot key so
every connected client refetches and drops the row from the loose-threads view.
"""

from __future__ import annotations

from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.state.persistence import history as history_store


class HistoryOps:
    """Thin wrappers over ``history_store`` keyed on an ``OrchestratorHost``."""

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

    async def dismiss(self, item_id: str) -> dict[str, Any]:
        assert self.rt.db is not None
        history_store.set_history_status(self.rt.db, item_id, "dismissed")
        return {"item_id": item_id, "status": "dismissed"}


__all__ = ["HistoryOps"]
