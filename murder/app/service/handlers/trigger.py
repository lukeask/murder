"""``trigger.*`` application handlers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from murder.app.protocol.lifecycle import TriggerFireParams, TriggerFireResult
from murder.app.protocol.requests import CommandName
from murder.state.persistence.triggers import enqueue_manual_trigger_fire

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _db() -> sqlite3.Connection:
        runtime = host.runtime
        if runtime is None or runtime.db is None:
            raise RuntimeError("service not started")
        return runtime.db

    def _fire(body: dict[str, object]) -> dict[str, object]:
        params = TriggerFireParams.model_validate(body)
        occurrence_key = enqueue_manual_trigger_fire(
            _db(),
            params.trigger_id,
            occurrence_key=params.occurrence_key,
            now=datetime.now(timezone.utc),
        )
        return TriggerFireResult(
            ok=True,
            trigger_id=str(params.trigger_id),
            occurrence_key=occurrence_key,
        ).model_dump(mode="json")

    host.register_application_command(CommandName.TRIGGER_FIRE, _fire)
