"""TUI preference and workflow application handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.protocol.workflows import (
    DeleteWorkflowParams,
    GetWorkflowsParams,
    PutWorkflowParams,
    SetWorkflowsParams,
    StartWorkflowParams,
)
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.connection import RepoDb

LOGGER = logging.getLogger(__name__)


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    *,
    repo_root: Path,
    db: RepoDb,
    orchestrator: Orchestrator,
) -> None:
    def _tui_prefs_file() -> Path:
        from murder.state.storage.paths import tui_prefs_path as _tui_prefs_path

        return _tui_prefs_path(repo_root)

    def _tui_load_favorites(_body: dict[str, Any]) -> dict[str, Any]:
        import json

        path = _tui_prefs_file()
        if not path.exists():
            return {"ok": True, "favorites": []}
        try:
            data = json.loads(path.read_text())
            favorites = data.get("favorites", [])
            if not isinstance(favorites, list):
                favorites = []
        except Exception:  # noqa: BLE001
            LOGGER.warning(
                "tui.load_favorites: failed to read/parse %s; returning empty list",
                path,
                exc_info=True,
            )
            favorites = []
        return {"ok": True, "favorites": [str(item) for item in favorites]}

    def _tui_save_favorites(body: dict[str, Any]) -> dict[str, Any]:
        import json

        favorites = body.get("favorites")
        if not isinstance(favorites, list):
            raise ValueError("tui.save_favorites requires favorites list")
        ids = sorted({str(item) for item in favorites})
        path = _tui_prefs_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"favorites": ids}))
        tmp.replace(path)
        return {"ok": True, "favorites": ids}

    def _tui_load_prompt_templates(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_prompt_templates

        return {"ok": True, "templates": load_prompt_templates()}

    def _tui_save_prompt_templates(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_prompt_templates

        templates = body.get("templates")
        if not isinstance(templates, list):
            raise ValueError("templates.set requires templates list")
        return {"ok": True, "templates": save_prompt_templates(templates)}

    def _tui_load_workflows(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import read_workflow_registry

        GetWorkflowsParams.model_validate(body or {})
        registry = read_workflow_registry()
        return {"ok": True, "workflows": registry.workflows, "revision": registry.revision}

    def _tui_save_workflows(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_workflows

        params = SetWorkflowsParams.model_validate(body)
        workflows = [item.model_dump(mode="json") for item in params.workflows]
        return {"ok": True, "workflows": save_workflows(workflows)}

    def _tui_put_workflow(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import put_workflow

        params = PutWorkflowParams.model_validate(body)
        mutation = put_workflow(
            params.workflow.model_dump(mode="json"),
            original_name=params.original_name,
            expected_revision=params.expected_revision,
        )
        workflow = params.workflow.model_dump(mode="json") if mutation.ok else None
        return {
            "ok": mutation.ok,
            "workflow": workflow,
            "workflows": mutation.workflows,
            "revision": mutation.revision,
            "issues": mutation.issues,
            "conflict": mutation.conflict,
        }

    def _tui_delete_workflow(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import delete_workflow

        params = DeleteWorkflowParams.model_validate(body)
        mutation = delete_workflow(
            params.name,
            expected_revision=params.expected_revision,
        )
        return {
            "ok": mutation.ok,
            "workflows": mutation.workflows,
            "revision": mutation.revision,
            "issues": mutation.issues,
            "conflict": mutation.conflict,
        }

    async def _tui_run_workflow(body: dict[str, Any]) -> dict[str, Any]:
        from murder.work.workflows.launch import run_workflow_by_name

        params = StartWorkflowParams.model_validate(body)
        name = params.name
        args = params.args

        try:
            result = run_workflow_by_name(db, repo_root, name, args)
        except KeyError as exc:
            # Turn the lookup miss into a client-facing message (KeyError's
            # repr would leak as a bare name. Mirrors other handlers'
            # bad-input -> ValueError contract.
            raise ValueError(f"no workflow named {name!r}") from exc

        # Kick only THIS run's stages: kickoff_ready(only=tid) spawns a stage
        # only if it's an eligible root, so downstream/dep-gated stages and
        # unrelated project tickets are left untouched.
        for tid in result.stage_ticket_ids.values():
            await orchestrator.kickoff_ready(only=tid)

        return {
            "ok": True,
            "workflow_id": result.workflow_id,
            "run_ticket_id": result.run_ticket_id,
            "stage_ticket_ids": result.stage_ticket_ids,
            "created_ticket_ids": result.created_ticket_ids,
        }

    def _tui_load_spawn_favorites(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_spawn_favorites

        return {"ok": True, "favorites": load_spawn_favorites()}

    def _tui_save_spawn_favorites(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_spawn_favorites

        favorites = body.get("favorites")
        if not isinstance(favorites, list):
            raise ValueError("tui.save_spawn_favorites requires favorites list")
        return {"ok": True, "favorites": save_spawn_favorites(favorites)}

    def _tui_load_themes(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_themes

        return {"ok": True, "themes": load_themes()}

    def _tui_save_themes(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_themes

        themes = body.get("themes")
        if not isinstance(themes, list):
            raise ValueError("tui.save_themes requires themes list")
        return {"ok": True, "themes": save_themes(themes)}

    def _tui_import_theme(body: dict[str, Any]) -> dict[str, Any]:
        from murder.app.protocol.settings import ImportThemeParams
        from murder.user_config import import_theme_from_json

        params = ImportThemeParams.model_validate(body)
        themes, new_id = import_theme_from_json(params.theme_json, theme_id=params.id)
        return {"ok": True, "themes": themes, "id": new_id}

    app.register_application_query(QueryName.FAVORITES_GET, _tui_load_favorites)
    app.register_application_query(QueryName.SPAWN_FAVORITES_GET, _tui_load_spawn_favorites)
    app.register_application_query(QueryName.TEMPLATES_GET, _tui_load_prompt_templates)
    app.register_application_query(QueryName.THEMES_GET, _tui_load_themes)
    app.register_application_query(QueryName.WORKFLOWS_GET, _tui_load_workflows)
    app.register_application_command(CommandName.FAVORITES_SET, _tui_save_favorites)
    app.register_application_command(CommandName.SPAWN_FAVORITES_SET, _tui_save_spawn_favorites)
    app.register_application_command(CommandName.TEMPLATES_SET, _tui_save_prompt_templates)
    app.register_application_command(CommandName.THEMES_SET, _tui_save_themes)
    app.register_application_command(CommandName.THEME_IMPORT, _tui_import_theme)
    app.register_application_command(CommandName.WORKFLOWS_SET, _tui_save_workflows)
    app.register_application_command(CommandName.WORKFLOW_PUT, _tui_put_workflow)
    app.register_application_command(CommandName.WORKFLOW_DELETE, _tui_delete_workflow)
    app.register_application_command(CommandName.WORKFLOW_START, _tui_run_workflow)
    projections.register(ProjectionTopic.FAVORITES, lambda: _tui_load_favorites({}))
    projections.register(ProjectionTopic.TEMPLATES, lambda: _tui_load_prompt_templates({}))
    projections.register(ProjectionTopic.THEMES, lambda: _tui_load_themes({}))
    projections.register(ProjectionTopic.WORKFLOWS, lambda: _tui_load_workflows({}))
