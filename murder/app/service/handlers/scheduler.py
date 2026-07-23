"""Scheduler application command registration."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from murder.app.protocol.requests import CommandName
from murder.app.protocol.session_control import SetSchedulerSteeringParams
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.scheduler_steering import set_steering


class SchedulerEffects(Protocol):
    db: sqlite3.Connection | None


def register(app: ApplicationRegistrar, effects: SchedulerEffects) -> None:
    def _set_scheduler_steering(body: dict[str, Any]) -> dict[str, Any]:
        if effects.db is None:
            raise RuntimeError("service runtime is unavailable")
        params = SetSchedulerSteeringParams.model_validate(body)
        return set_steering(effects.db, harness=params.harness, steering=params.steering)

    app.register_application_command(
        CommandName.SCHEDULER_SET_STEERING,
        _set_scheduler_steering,
    )


__all__ = ["SchedulerEffects", "register"]
