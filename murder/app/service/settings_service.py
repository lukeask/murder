"""Service-side settings persistence and model discovery (W2/W9).

This is the single persistence policy for user-scope settings. Handlers commit
through ``SettingsRepository.save``; harness-doc regeneration remains part of
save so catalog docs stay current. Model-catalog refresh is opt-in via
``schedule_model_refresh`` so settings.update is not coupled to discovery I/O.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from murder.app.service.settings.ports import LiveChange, apply_live_changes
from murder.config import Config, HarnessKind
from murder.llm.harness_control.runtime.live_model_probe import (
    LIVE_MODEL_DISCOVERY_HARNESSES,
    probe_live_models,
)
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import (
    CATALOG_ADVISORY,
    get_available_models,
    refresh_and_persist_harness_models,
)
from murder.user_config import UserConfig, load_user_config, save_user_config

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SettingsApplyResult:
    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ModelDiscoveryResult:
    ok: bool
    models: tuple[tuple[str, str], ...]
    message: str | None = None


@dataclass
class HostLiveConfig:
    """LiveConfigPort backed by the running service ``Config``."""

    config: Config

    def apply(self, changes: Sequence[LiveChange]) -> None:
        apply_live_changes(self.config, changes)


@dataclass
class SettingsService:
    """Owns user config writes and configured harness model catalog access.

    Harness/model selection is user-scope only; there is no project
    ``roles.yaml`` write path.
    """

    repo_root: Path

    def load(self) -> UserConfig:
        return load_user_config()

    def save(self, config: UserConfig) -> None:
        """Persist user config and regenerate harness catalog docs."""
        save_user_config(config)
        write_harnesses_doc(self.repo_root)

    def save_global(self, user_config: UserConfig) -> SettingsApplyResult:
        """Persist + regenerate docs; schedule catalog refresh on success."""
        try:
            self.save(user_config)
        except OSError as exc:
            LOGGER.exception("failed to save user config")
            return SettingsApplyResult(ok=False, error=str(exc))
        self.schedule_model_refresh()
        return SettingsApplyResult(ok=True)

    def schedule_model_refresh(self) -> None:
        """Persist the configured model catalog on the running event loop.

        Best-effort: if no running loop exists (e.g. called from a sync test
        in a non-async context), the refresh is silently skipped. DB
        persistence is skipped here since ``SettingsService`` has no DB
        reference; the service startup and reconfiguration paths persist it.
        """
        repo_root = self.repo_root
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            loop.create_task(
                refresh_and_persist_harness_models(repo_root, db=None),
                name="settings-model-refresh",
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "failed to schedule configured model catalog refresh after settings save",
                exc_info=True,
            )

    async def discover_models(self, harness: HarnessKind | str) -> ModelDiscoveryResult:
        kind = harness if isinstance(harness, str) else str(harness)
        if kind not in REGISTRY:
            return ModelDiscoveryResult(
                ok=False,
                models=(),
                message=f"unknown harness {kind!r}; no configured model catalog",
            )
        if kind in LIVE_MODEL_DISCOVERY_HARNESSES:
            result = await probe_live_models(kind, self.repo_root)
            return ModelDiscoveryResult(result.ok, result.models, result.message)
        return ModelDiscoveryResult(
            ok=True,
            models=tuple(get_available_models(kind)),
            message=CATALOG_ADVISORY,
        )


__all__ = [
    "HostLiveConfig",
    "ModelDiscoveryResult",
    "SettingsApplyResult",
    "SettingsService",
]
