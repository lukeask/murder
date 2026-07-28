"""Built-in workflow templates that are always available (not user-persisted).

The launch-oriented ``ticket`` template is the one-node workflow behind “new ticket”
when configured harness/model defaults make it runnable. User registries never store
these; they are merged into ``workflows.get`` replies and resolved by name at start.
"""

from __future__ import annotations

from typing import Any

from murder.work.workflows.definition import StageDef, WorkflowDef

TICKET_WORKFLOW_NAME = "ticket"


def ticket_workflow_template() -> WorkflowDef:
    """Return a fresh copy of the built-in one-node ticket workflow template."""
    return WorkflowDef(
        name=TICKET_WORKFLOW_NAME,
        builtin=True,
        description="A single work item",
        stages=[
            StageDef(
                id="work",
                title="{title}",
                instructions="{prompt}",
            )
        ],
    )


def builtin_workflow_templates() -> dict[str, WorkflowDef]:
    """Name → fresh template instance for every built-in workflow."""
    return {TICKET_WORKFLOW_NAME: ticket_workflow_template()}


def get_builtin_workflow(name: str) -> WorkflowDef | None:
    """Return a fresh built-in template for *name*, or ``None``."""
    templates = builtin_workflow_templates()
    found = templates.get(name)
    return None if found is None else found.model_copy(deep=True)


def is_builtin_workflow_name(name: str) -> bool:
    return name in builtin_workflow_templates()


def merge_builtin_workflows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlay built-ins onto a persisted registry list (built-ins win on name clash)."""
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        name = record.get("name")
        if not isinstance(name, str) or is_builtin_workflow_name(name):
            continue
        by_name[name] = record
    for name, defn in builtin_workflow_templates().items():
        by_name[name] = defn.model_dump(mode="json")
    return [by_name[n] for n in sorted(by_name)]


def configured_execution_defaults() -> tuple[str | None, str | None]:
    """Return ``(harness, model)`` from user config when both are usable for launch.

    Prefers ``tui.startup_rogue``. An empty startup model falls back to the first
    catalog entry for that harness. ``(None, None)`` means defaults are not runnable.
    """
    from murder.user_config import load_user_config  # noqa: PLC0415

    cfg = load_user_config()
    sr = cfg.tui.startup_rogue
    if sr is None:
        return None, None
    harness = str(sr.harness).strip() if sr.harness else ""
    if not harness:
        return None, None
    model = (sr.model or "").strip()
    if not model:
        from murder.app.service.settings.view import build_startup_rogue_catalogs  # noqa: PLC0415

        models_by_harness, _ = build_startup_rogue_catalogs()
        entries = models_by_harness.get(harness) or []
        if entries:
            model = str(entries[0].get("id") or "").strip()
    if not model:
        return harness, None
    return harness, model


def apply_launch_execution(
    defn: WorkflowDef,
    args: dict[str, str] | None = None,
    *,
    default_harness: str | None = None,
    default_model: str | None = None,
) -> WorkflowDef:
    """Fill missing stage harness/model/worktree from args or configured defaults.

    Args keys ``harness`` / ``model`` / ``worktree`` override defaults for every stage
    that still lacks that field. Placeholder substitution for title/instructions is
    unchanged (handled at materialize time).
    """
    args = args or {}
    harness = (args.get("harness") or "").strip() or default_harness
    model = (args.get("model") or "").strip() or default_model
    worktree_arg = args.get("worktree")
    worktree = worktree_arg.strip() if isinstance(worktree_arg, str) and worktree_arg.strip() else None

    stages: list[StageDef] = []
    for stage in defn.stages:
        stages.append(
            stage.model_copy(
                update={
                    "harness": stage.harness or harness,
                    "model": stage.model or model,
                    "worktree": stage.worktree if stage.worktree is not None else worktree,
                }
            )
        )
    return defn.model_copy(update={"stages": stages})


def prepare_workflow_for_launch(
    defn: WorkflowDef,
    args: dict[str, str] | None = None,
) -> WorkflowDef:
    """Resolve execution fields so ``validate_workflow`` accepts the definition at start."""
    default_harness, default_model = configured_execution_defaults()
    resolved = apply_launch_execution(
        defn,
        args,
        default_harness=default_harness,
        default_model=default_model,
    )
    return resolved


__all__ = [
    "TICKET_WORKFLOW_NAME",
    "apply_launch_execution",
    "builtin_workflow_templates",
    "configured_execution_defaults",
    "get_builtin_workflow",
    "is_builtin_workflow_name",
    "merge_builtin_workflows",
    "prepare_workflow_for_launch",
    "ticket_workflow_template",
]
