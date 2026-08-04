"""Scheduler application command registration."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.requests import CommandName
from murder.app.protocol.session_control import SetSchedulerSteeringParams
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.scheduler_steering import set_steering
from murder.state.persistence.connection import RepoDb


def register(app: ApplicationRegistrar, db: RepoDb) -> None:
    def _set_scheduler_steering(body: dict[str, Any]) -> dict[str, Any]:
        params = SetSchedulerSteeringParams.model_validate(body)
        return set_steering(db, harness=params.harness, steering=params.steering)

    app.register_application_command(
        CommandName.SCHEDULER_SET_STEERING,
        _set_scheduler_steering,
    )


__all__ = ["register"]
