"""``trigger.*`` RPC handlers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from murder.state.persistence.triggers import enqueue_manual_trigger_fire

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FireTriggerParams(_Params):
    trigger_id: UUID
    occurrence_key: str | None = Field(default=None, min_length=1)


def register(host: ServiceHost) -> None:
    def _db() -> sqlite3.Connection:
        runtime = host.runtime
        if runtime is None or runtime.db is None:
            raise RuntimeError("service not started")
        return runtime.db

    def _fire(body: dict[str, object]) -> dict[str, object]:
        params = FireTriggerParams.model_validate(body)
        occurrence_key = enqueue_manual_trigger_fire(
            _db(),
            params.trigger_id,
            occurrence_key=params.occurrence_key,
            now=datetime.now(timezone.utc),
        )
        return {
            "ok": True,
            "trigger_id": str(params.trigger_id),
            "occurrence_key": occurrence_key,
        }

    host.register_rpc_handler("trigger.fire", _fire)
