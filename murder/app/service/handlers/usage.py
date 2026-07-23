"""Harness usage sampling application command registration."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Protocol

from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.usage_sampling import sample_usage


class UsageEffects(Protocol):
    repo_root: Path
    db: sqlite3.Connection | None


def register(app: ApplicationRegistrar, effects: UsageEffects) -> None:
    async def _sample_usage(body: dict[str, Any]) -> dict[str, Any]:
        if effects.db is None:
            raise RuntimeError("service runtime is unavailable")
        raw_modes = body.get("modes")
        if raw_modes is not None and not isinstance(raw_modes, list):
            raise ValueError(
                "state.harness_usage.sample modes must be a list when provided"
            )
        modes = (
            {str(mode) for mode in raw_modes}
            if raw_modes is not None
            else None
        )
        return await sample_usage(
            repo_root=effects.repo_root,
            db=effects.db,
            modes=modes,
        )

    app.register_application_command(
        CommandName.HARNESS_USAGE_SAMPLE,
        _sample_usage,
    )


__all__ = ["UsageEffects", "register"]
