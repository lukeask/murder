"""``tui.*`` RPC handlers (favorites, templates, workflows, spawn favorites)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.workflows import (
    GetWorkflowsParams,
    SetWorkflowsParams,
    StartWorkflowParams,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost

LOGGER = logging.getLogger(__name__)


def register(host: ServiceHost) -> None:
    def _tui_prefs_file() -> Path:
        from murder.state.storage.paths import tui_prefs_path as _tui_prefs_path

        return _tui_prefs_path(host.repo_root)

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

    def _tui_load_templates(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_templates

        return {"ok": True, "templates": load_templates()}

    def _tui_save_templates(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_templates

        templates = body.get("templates")
        if not isinstance(templates, list):
            raise ValueError("tui.save_templates requires templates list")
        return {"ok": True, "templates": save_templates(templates)}

    def _tui_load_workflows(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_workflows

        GetWorkflowsParams.model_validate(body or {})
        return {"ok": True, "workflows": load_workflows()}

    def _tui_save_workflows(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_workflows

        params = SetWorkflowsParams.model_validate(body)
        workflows = [item.model_dump(mode="json") for item in params.workflows]
        return {"ok": True, "workflows": save_workflows(workflows)}

    async def _tui_run_workflow(body: dict[str, Any]) -> dict[str, Any]:
        from murder.work.workflows.launch import run_workflow_by_name

        params = StartWorkflowParams.model_validate(body)
        name = params.name
        args = params.args

        # Single start guard covering runtime+db+orchestrator, matching the
        # sibling handlers' message. (orchestrator and runtime are set
        # together at startup, so a pre-start request would otherwise leak
        # the internal "orchestrator unavailable" error instead.)
        if host.runtime is None or host.runtime.db is None or host.orchestrator is None:
            raise RuntimeError("service not started")
        orchestrator = host.orchestrator
        db = host.runtime.db

        try:
            result = run_workflow_by_name(db, host.repo_root, name, args)
        except KeyError:
            # Turn the lookup miss into a client-facing message (KeyError's
            # repr would leak as a bare name); mirrors other handlers'
            # bad-input -> ValueError contract.
            raise ValueError(f"no saved workflow named {name!r}")

        # Kick only THIS run's stages: kickoff_ready(only=tid) spawns a stage
        # only if it's an eligible root, so downstream/dep-gated stages and
        # unrelated project tickets are left untouched.
        for tid in result.stage_ticket_ids.values():
            await orchestrator.kickoff_ready(only=tid)

        return {
            "ok": True,
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
        from murder.user_config import import_theme_from_json

        json_str = body.get("json")
        if not isinstance(json_str, str) or not json_str.strip():
            raise ValueError("tui.import_theme requires non-empty json string")
        theme_id = body.get("id")
        if theme_id is not None and not isinstance(theme_id, str):
            raise ValueError("tui.import_theme id must be a string when provided")
        themes, new_id = import_theme_from_json(json_str, theme_id=theme_id)
        return {"ok": True, "themes": themes, "id": new_id}

    host.register_application_query(QueryName.FAVORITES_GET, _tui_load_favorites)
    host.register_application_query(
        QueryName.SPAWN_FAVORITES_GET, _tui_load_spawn_favorites
    )
    host.register_application_query(QueryName.TEMPLATES_GET, _tui_load_templates)
    host.register_application_query(QueryName.THEMES_GET, _tui_load_themes)
    host.register_application_query(QueryName.WORKFLOWS_GET, _tui_load_workflows)
    host.register_application_command(CommandName.FAVORITES_SET, _tui_save_favorites)
    host.register_application_command(
        CommandName.SPAWN_FAVORITES_SET, _tui_save_spawn_favorites
    )
    host.register_application_command(CommandName.TEMPLATES_SET, _tui_save_templates)
    host.register_application_command(CommandName.THEMES_SET, _tui_save_themes)
    host.register_application_command(CommandName.THEME_IMPORT, _tui_import_theme)
    host.register_application_command(CommandName.WORKFLOWS_SET, _tui_save_workflows)
    host.register_application_command(CommandName.WORKFLOW_START, _tui_run_workflow)
