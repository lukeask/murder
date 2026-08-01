"""Launch a saved or built-in workflow by name into the current project.

The userspace registry (``~/.config/murder/workflows.yaml``) stores reusable
``WorkflowTemplate`` (``WorkflowDef``) dumps. Launching one resolves it by
name (built-ins first), fills harness/model defaults for built-ins, compiles
it authoritatively (expand ``:foo:``, resolve inputs), and hands the expanded
snapshot to ``materialize_workflow``. This module is the thin name→definition
lookup that sits in front of that deep module, so the RPC handler stays a shell.
"""

from __future__ import annotations

from pathlib import Path

from murder.state.persistence.connection import RepoDb
from murder.work.workflows.compile import (
    apply_input_defaults,
    compile_workflow_template,
    prompt_template_map,
    required_input_issues,
)
from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.materialize import MaterializeResult, materialize_workflow


def run_workflow_by_name(
    db: RepoDb,
    repo_root: Path,
    name: str,
    args: dict[str, str] | None = None,
    *,
    prompt_templates: dict[str, str] | None = None,
) -> MaterializeResult:
    """Load workflow *name* (built-in or userspace registry), prepare, and start.

    Raises KeyError if no workflow with that name exists. Raises ValueError if the
    definition is invalid, launch defaults cannot make it runnable, or
    compile/start validation fails.
    """
    # Lazy imports keep this module free of cycles: user_config pulls in config
    # machinery, and definition/builtins are only needed at call time.
    from murder.user_config import load_workflows  # noqa: PLC0415
    from murder.work.workflows.builtins import (  # noqa: PLC0415
        get_builtin_workflow,
        prepare_workflow_for_launch,
    )
    from murder.work.workflows.definition import validate_workflow  # noqa: PLC0415

    defn = get_builtin_workflow(name)
    if defn is None:
        # Last match wins, mirroring save_workflows' "last dupe wins" normalization,
        # so a launch sees the same definition a re-save would persist.
        found: dict | None = None
        for d in load_workflows():
            if d.get("name") == name:
                found = d
        if found is None:
            raise KeyError(name)
        defn = WorkflowDef.model_validate(found)

    resolved = prepare_workflow_for_launch(defn, args)
    # Built-ins omit harness/model until launch. After prepare they must be concrete.
    errors = validate_workflow(resolved.model_copy(update={"builtin": False}))
    if errors:
        raise ValueError("invalid workflow: " + ". ".join(errors))

    return start_workflow_from_def(
        db,
        repo_root,
        resolved,
        args,
        prompt_templates=prompt_templates,
    )


def start_workflow_from_def(
    db: RepoDb,
    repo_root: Path,
    defn: WorkflowDef,
    args: dict[str, str] | None = None,
    *,
    prompt_templates: dict[str, str] | None = None,
) -> MaterializeResult:
    """Compile *defn* authoritatively, then materialize the expanded snapshot.

    The run's ``definition_snapshot`` retains expanded ``:foo:`` text with
    unresolved ``{inputs}``; ``args`` (merged with defaults) are substituted
    when writing tickets. Later edits to prompt/workflow templates do not
    affect an already-created run.
    """
    templates = prompt_templates if prompt_templates is not None else prompt_template_map()
    compiled = compile_workflow_template(defn, prompt_templates=templates)
    if not compiled.ok:
        messages = [issue.message for issue in compiled.issues if issue.severity == "error"]
        raise ValueError("workflow compile failed: " + ". ".join(messages))

    merged = apply_input_defaults(compiled.inputs, args)
    missing = required_input_issues(compiled.inputs, merged)
    if missing:
        raise ValueError(
            "workflow start blocked: " + ". ".join(issue.message for issue in missing)
        )

    return materialize_workflow(db, repo_root, compiled.expanded_template, merged)
