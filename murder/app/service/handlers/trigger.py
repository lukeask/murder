"""``trigger.*`` application handlers."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.app.protocol.lifecycle import TriggerFireParams, TriggerFireResult
from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.triggers import enqueue_manual_trigger_fire


def register(app: ApplicationRegistrar, db: RepoDb) -> None:
    def _fire(body: dict[str, object]) -> dict[str, object]:
        params = TriggerFireParams.model_validate(body)
        occurrence_key = enqueue_manual_trigger_fire(
            db,
            params.trigger_id,
            occurrence_key=params.occurrence_key,
            now=datetime.now(timezone.utc),
        )
        return TriggerFireResult(
            ok=True,
            trigger_id=str(params.trigger_id),
            occurrence_key=occurrence_key,
        ).model_dump(mode="json")

    app.register_application_command(CommandName.TRIGGER_FIRE, _fire)
