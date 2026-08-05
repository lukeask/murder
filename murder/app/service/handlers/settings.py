"""``settings.get`` / ``settings.update`` application handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.settings import (
    apply_settings_patch,
    build_settings_payload,
    commit_settings_mutation,
    effective_harness_view,
)
from murder.app.service.settings_service import HostLiveConfig, SettingsService
from murder.config import Config


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    *,
    repo_root: Path,
    config: Config,
) -> None:
    def _repository() -> SettingsService:
        return SettingsService(repo_root=repo_root)

    def _live() -> HostLiveConfig:
        return HostLiveConfig(config=config)

    def _settings_get(_body: dict[str, Any]) -> dict[str, Any]:
        cfg = _repository().load()
        project = config.project.name if config.project is not None else None
        return {
            "ok": True,
            "settings": build_settings_payload(
                cfg,
                effective=effective_harness_view(config),
                project=project,
            ),
        }

    def _settings_update(body: dict[str, Any]) -> dict[str, Any]:
        partial = body.get("settings")
        if not isinstance(partial, dict):
            raise ValueError("settings.update requires a settings object")
        mutation = apply_settings_patch(_repository().load(), partial)
        commit_settings_mutation(mutation, _repository(), _live())
        # NOTE: llm env changes are NOT applied live. They take effect at next
        # daemon start via apply_llm_env in Config.load.
        project = config.project.name if config.project is not None else None
        return {
            "ok": True,
            "settings": build_settings_payload(
                mutation.config,
                effective=effective_harness_view(config),
                project=project,
            ),
        }

    app.register_application_query(QueryName.SETTINGS_GET, _settings_get)
    app.register_application_command(CommandName.SETTINGS_UPDATE, _settings_update)
    projections.register(ProjectionTopic.SETTINGS, lambda: _settings_get({}))
