"""Service-side settings persistence and model discovery (W2/W9)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from murder.config import HarnessKind, HarnessRoleConfig
from murder.llm.harnesses.harnesses_doc import write_harnesses_doc
from murder.llm.harnesses.model_discovery import discover_harness_models
from murder.state.storage.paths import roles_yaml
from murder.user_config import UserConfig, save_user_config

LOGGER = logging.getLogger(__name__)

Scope = Literal["global", "project"]


@dataclass(frozen=True, slots=True)
class SettingsApplyResult:
    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ModelDiscoveryResult:
    ok: bool
    models: tuple[tuple[str, str], ...]
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectRoleModels:
    crow_handler_model: str
    collaborator_harness: HarnessKind
    notetaker_model: str
    crow_handler_auto_free: bool = False
    notetaker_provider: str = "cerebras"
    notetaker_auto_free: bool = False
    planner_harness: HarnessKind = "claude_code"


@dataclass
class SettingsService:
    """Owns user config and project ``roles.yaml`` writes."""

    repo_root: Path

    def save_global(self, user_config: UserConfig) -> SettingsApplyResult:
        try:
            save_user_config(user_config)
        except OSError as exc:
            LOGGER.exception("failed to save user config")
            return SettingsApplyResult(ok=False, error=str(exc))
        write_harnesses_doc(self.repo_root)
        return SettingsApplyResult(ok=True)

    def save_project(
        self,
        *,
        default_crow: dict[str, Any],
        role_models: ProjectRoleModels,
    ) -> SettingsApplyResult:
        path = roles_yaml(self.repo_root)
        if not path.exists():
            return SettingsApplyResult(
                ok=False,
                error="No .murder/roles.yaml — run murder init first.",
            )
        try:
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raw = {}
            HarnessRoleConfig.model_validate(default_crow)
            raw["default_crow"] = default_crow
            self._apply_role_models(raw, role_models)
            path.write_text(
                yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as exc:
            LOGGER.exception("failed to save project roles")
            return SettingsApplyResult(ok=False, error=str(exc))
        write_harnesses_doc(self.repo_root)
        return SettingsApplyResult(ok=True)

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

    @staticmethod
    def _apply_role_models(raw: dict[str, Any], role_models: ProjectRoleModels) -> None:
        crow_handler = raw.get("crow_handler")
        if not isinstance(crow_handler, dict):
            crow_handler = {}
        crow_handler["model"] = role_models.crow_handler_model
        crow_handler["auto_free"] = role_models.crow_handler_auto_free
        raw["crow_handler"] = crow_handler

        collaborator = raw.get("collaborator")
        if not isinstance(collaborator, dict):
            collaborator = {}
        collaborator["harness"] = role_models.collaborator_harness
        raw["collaborator"] = collaborator

        notetaker = raw.get("notetaker")
        if not isinstance(notetaker, dict):
            notetaker = {}
        notetaker["provider"] = role_models.notetaker_provider
        notetaker["model"] = role_models.notetaker_model
        notetaker["auto_free"] = role_models.notetaker_auto_free
        raw["notetaker"] = notetaker

        planner = raw.get("planner")
        if not isinstance(planner, dict):
            planner = {}
        planner["harness"] = role_models.planner_harness
        raw["planner"] = planner


async def apply_settings_change(
    service: SettingsService,
    *,
    scope: Scope,
    changes: dict[str, object],
) -> SettingsApplyResult:
    """RPC/command entry: apply a typed settings change by scope."""
    if scope == "global":
        user = changes.get("user_config")
        if not isinstance(user, UserConfig):
            return SettingsApplyResult(ok=False, error="global scope requires user_config")
        return service.save_global(user)
    if scope == "project":
        crow = changes.get("default_crow")
        roles = changes.get("role_models")
        if not isinstance(crow, dict) or not isinstance(roles, ProjectRoleModels):
            return SettingsApplyResult(ok=False, error="project scope requires default_crow and role_models")
        return service.save_project(default_crow=crow, role_models=roles)
    return SettingsApplyResult(ok=False, error=f"unknown settings scope: {scope}")


__all__ = [
    "ModelDiscoveryResult",
    "ProjectRoleModels",
    "SettingsApplyResult",
    "SettingsService",
    "apply_settings_change",
]
