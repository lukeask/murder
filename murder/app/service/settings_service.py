"""Service-side settings persistence and model discovery (W2/W9)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from murder.config import HarnessKind
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_cache import (
    CATALOG_ADVISORY,
    get_available_models,
    refresh_and_persist_harness_models,
)
from murder.user_config import UserConfig, save_user_config

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
class SettingsService:
    """Owns user config writes and configured harness model catalog access.

    Harness/model selection is user-scope only; there is no project
    ``roles.yaml`` write path.
    """

    repo_root: Path

    def save_global(self, user_config: UserConfig) -> SettingsApplyResult:
        try:
            save_user_config(user_config)
        except OSError as exc:
            LOGGER.exception("failed to save user config")
            return SettingsApplyResult(ok=False, error=str(exc))
        write_harnesses_doc(self.repo_root)
        self._schedule_model_refresh()
        return SettingsApplyResult(ok=True)

    def _schedule_model_refresh(self) -> None:
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
        return ModelDiscoveryResult(
            ok=True,
            models=tuple(get_available_models(kind)),
            message=CATALOG_ADVISORY,
        )


__all__ = [
    "ModelDiscoveryResult",
    "SettingsApplyResult",
    "SettingsService",
]
