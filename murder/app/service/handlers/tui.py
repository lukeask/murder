"""``tui.*`` RPC handlers (favorites, templates, workflows, spawn favorites)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

    def _tui_load_workflows(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import load_workflows

        return {"ok": True, "workflows": load_workflows()}

    def _tui_save_workflows(body: dict[str, Any]) -> dict[str, Any]:
        from murder.user_config import save_workflows

        workflows = body.get("workflows")
        if not isinstance(workflows, list):
            raise ValueError("tui.save_workflows requires workflows list")
        return {"ok": True, "workflows": save_workflows(workflows)}

    async def _tui_run_workflow(body: dict[str, Any]) -> dict[str, Any]:
        from murder.bus import Entity
        from murder.work.workflows.launch import run_workflow_by_name

        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("tui.run_workflow requires name")
        raw_args = body.get("args")
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raise ValueError("tui.run_workflow args must be an object")
        # Placeholder substitution is string-only; coerce so a numeric/bool
        # arg from the wire still fills a ``{key}`` token cleanly.
        args = {str(k): str(v) for k, v in raw_args.items()}

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

        # Publish every freshly created ticket so the frontend renders the
        # new run tree before any crow spawns.
        for tid in result.created_ticket_ids:
            await host.runtime.publish_snapshot(Entity.TICKET, tid)

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

    host.register_rpc_handler("tui.load_favorites", _tui_load_favorites)
    host.register_rpc_handler("tui.save_favorites", _tui_save_favorites)
    host.register_rpc_handler("tui.load_templates", _tui_load_templates)
    host.register_rpc_handler("tui.save_templates", _tui_save_templates)
    host.register_rpc_handler("tui.load_workflows", _tui_load_workflows)
    host.register_rpc_handler("tui.save_workflows", _tui_save_workflows)
    host.register_rpc_handler("tui.run_workflow", _tui_run_workflow)
    host.register_rpc_handler("tui.load_spawn_favorites", _tui_load_spawn_favorites)
    host.register_rpc_handler("tui.save_spawn_favorites", _tui_save_spawn_favorites)
