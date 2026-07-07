"""Service-side settings persistence and model discovery (W2/W9)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from murder.config import HarnessKind
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_discovery import discover_harness_models
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
    """Owns user config writes and harness model discovery.

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
        """Fire-and-forget model re-discovery on the running event loop.

        Best-effort: if no running loop exists (e.g. called from a sync test
        in a non-async context), the refresh is silently
        skipped.  DB persistence is skipped here since ``SettingsService`` has
        no DB reference; the full persist path goes through
        ``reconfigure_collaborator`` (which is async and holds the DB).
        """
        import asyncio

        from murder.llm.harnesses.model_cache import refresh_and_persist_harness_models

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
                "failed to schedule model refresh after settings save"
                " (UI model list will be stale until next refresh)",
                exc_info=True,
            )

    async def discover_models(self, harness: HarnessKind | str) -> ModelDiscoveryResult:
        kind = harness if isinstance(harness, str) else str(harness)
        result = await discover_harness_models(kind, self.repo_root)
        if not result.ok or not result.data:
            return ModelDiscoveryResult(
                ok=False,
                models=(),
                message=result.message or f"{harness} model discovery failed",
            )
        return ModelDiscoveryResult(
            ok=True,
            models=tuple(result.data),
            message=None,
        )


__all__ = [
    "ModelDiscoveryResult",
    "SettingsApplyResult",
    "SettingsService",
]
