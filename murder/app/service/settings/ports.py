"""Settings persistence and live-application ports.

Inspectable ``LiveChange`` values replace deferred ``list[Callable]`` so
null/clear symmetry, ordering, and failure behavior stay explicit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, get_args

from murder.config import Config, HarnessKind, _load_bundled_defaults
from murder.user_config import UserConfig


@dataclass(frozen=True, slots=True)
class SetCollaboratorHarness:
    harness: HarnessKind


@dataclass(frozen=True, slots=True)
class ClearCollaboratorHarness:
    """Restore the live collaborator harness to the bundled default."""


@dataclass(frozen=True, slots=True)
class SetPlannerHarness:
    harness: HarnessKind


@dataclass(frozen=True, slots=True)
class ClearPlannerHarness:
    """Restore the live planner harness to the bundled default."""


@dataclass(frozen=True, slots=True)
class SetCrowHarnesses:
    harness: HarnessKind
    harnesses: tuple[HarnessKind, ...] | None


@dataclass(frozen=True, slots=True)
class ClearCrowHarnesses:
    """Restore the live crow harness selection to the bundled default."""


LiveChange = (
    SetCollaboratorHarness
    | ClearCollaboratorHarness
    | SetPlannerHarness
    | ClearPlannerHarness
    | SetCrowHarnesses
    | ClearCrowHarnesses
)


@dataclass(frozen=True, slots=True)
class SettingsMutation:
    config: UserConfig
    live_changes: tuple[LiveChange, ...]


class SettingsRepository(Protocol):
    """Single persistence policy for user-scope settings."""

    def load(self) -> UserConfig: ...

    def save(self, config: UserConfig) -> None: ...


class LiveConfigPort(Protocol):
    def apply(self, changes: Sequence[LiveChange]) -> None: ...


def bundled_role_selection(role: str) -> tuple[HarnessKind, list[HarnessKind] | None]:
    """Return ``(harness, harnesses)`` for *role* from bundled ``roles.yaml``."""
    valid = set(get_args(HarnessKind))
    block = _load_bundled_defaults().get(role) or {}
    harness = block.get("harness")
    if not isinstance(harness, str) or harness not in valid:
        raise RuntimeError(f"bundled roles.yaml missing harness for {role!r}")
    raw_harnesses = block.get("harnesses")
    if not isinstance(raw_harnesses, list) or not raw_harnesses:
        return harness, None  # type: ignore[return-value]
    harnesses: list[HarnessKind] = []
    for item in raw_harnesses:
        if not isinstance(item, str) or item not in valid:
            raise RuntimeError(
                f"bundled roles.yaml has invalid harness {item!r} for {role!r}"
            )
        harnesses.append(item)  # type: ignore[arg-type]
    return harness, harnesses  # type: ignore[return-value]


def apply_live_changes(live: Config, changes: Sequence[LiveChange]) -> None:
    """Apply inspectable live mutations onto the running ``Config``."""
    for change in changes:
        if isinstance(change, SetCollaboratorHarness):
            live.collaborator.harness = change.harness
        elif isinstance(change, ClearCollaboratorHarness):
            harness, _ = bundled_role_selection("collaborator")
            live.collaborator.harness = harness
        elif isinstance(change, SetPlannerHarness):
            live.planner.harness = change.harness
        elif isinstance(change, ClearPlannerHarness):
            harness, _ = bundled_role_selection("planner")
            live.planner.harness = harness
        elif isinstance(change, SetCrowHarnesses):
            live.default_crow.harness = change.harness
            live.default_crow.harnesses = (
                list(change.harnesses) if change.harnesses is not None else None
            )
        elif isinstance(change, ClearCrowHarnesses):
            harness, harnesses = bundled_role_selection("default_crow")
            live.default_crow.harness = harness
            live.default_crow.harnesses = harnesses
        else:  # pragma: no cover - exhaustive for LiveChange
            raise TypeError(f"unknown live change: {type(change)!r}")


def commit_settings_mutation(
    mutation: SettingsMutation,
    repository: SettingsRepository,
    live: LiveConfigPort,
) -> None:
    """Persist first, then apply live mutations (no divergence on save failure)."""
    repository.save(mutation.config)
    live.apply(mutation.live_changes)


__all__ = [
    "ClearCollaboratorHarness",
    "ClearCrowHarnesses",
    "ClearPlannerHarness",
    "LiveChange",
    "LiveConfigPort",
    "SetCollaboratorHarness",
    "SetCrowHarnesses",
    "SetPlannerHarness",
    "SettingsMutation",
    "SettingsRepository",
    "apply_live_changes",
    "bundled_role_selection",
    "commit_settings_mutation",
]
