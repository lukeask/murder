"""Session-adjacent scheduler and harness-usage application contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from murder.app.protocol.common import ApplicationModel


class SampleHarnessUsageParams(ApplicationModel):
    modes: list[str] | None = None


class SampleHarnessUsageResult(ApplicationModel):
    handled: Literal[True] = True
    stored: int
    failures: int
    sampled_kinds: list[str]


class SetSchedulerSteeringParams(ApplicationModel):
    harness: str = Field(min_length=1)
    steering: Literal["auto", "pause", "prefer"]

    @field_validator("harness")
    @classmethod
    def strip_harness(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("harness must be non-empty")
        return text


class SetSchedulerSteeringResult(ApplicationModel):
    handled: Literal[True] = True
    harness: str
    steering: Literal["auto", "pause", "prefer"]


__all__ = [
    "SampleHarnessUsageParams",
    "SampleHarnessUsageResult",
    "SetSchedulerSteeringParams",
    "SetSchedulerSteeringResult",
]
