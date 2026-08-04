"""Harness usage sampling application command registration."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.requests import CommandName
from murder.app.protocol.session_control import SampleHarnessUsageParams
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.usage_sampling import UsageSamplingService


def register(app: ApplicationRegistrar, usage: UsageSamplingService) -> None:
    async def _sample_usage(body: dict[str, Any]) -> dict[str, Any]:
        params = SampleHarnessUsageParams.model_validate(body or {})
        modes = set(params.modes) if params.modes is not None else None
        return await usage.sample(modes=modes)

    app.register_application_command(
        CommandName.HARNESS_USAGE_SAMPLE,
        _sample_usage,
    )


__all__ = ["register"]
