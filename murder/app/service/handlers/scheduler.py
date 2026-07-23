"""Scheduler application command registration."""

from __future__ import annotations

import sqlite3
from typing import Any, Protocol

from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.scheduler_steering import set_steering


class SchedulerEffects(Protocol):
    db: sqlite3.Connection | None


def register(app: ApplicationRegistrar, effects: SchedulerEffects) -> None:
    def _set_scheduler_steering(body: dict[str, Any]) -> dict[str, Any]:
        if effects.db is None:
            raise RuntimeError("service runtime is unavailable")
        harness = body.get("harness")
        steering = body.get("steering")
        if not isinstance(harness, str) or not isinstance(steering, str):
            raise ValueError(
                "scheduler.set_steering requires harness and steering strings"
            )
        return set_steering(effects.db, harness=harness, steering=steering)

    app.register_application_command(
        CommandName.SCHEDULER_SET_STEERING,
        _set_scheduler_steering,
    )


__all__ = ["SchedulerEffects", "register"]
