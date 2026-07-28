"""Launch a saved workflow by name into the current project.

The userspace registry (``~/.config/murder/workflows.yaml``) stores reusable
``WorkflowDef`` dumps; launching one resolves it by name, compiles it
authoritatively (expand ``:foo:``, resolve inputs), and hands the expanded
snapshot to ``materialize_workflow``. This module is the thin name→definition
lookup that sits in front of that deep module, so the RPC handler stays a shell.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.work.workflows.compile import (
    apply_input_defaults,
    compile_workflow_template,
    prompt_template_map,
    required_input_issues,
)
from murder.work.workflows.definition import WorkflowDef
from murder.work.workflows.materialize import MaterializeResult, materialize_workflow


def run_workflow_by_name(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    args: dict[str, str] | None = None,
    *,
    prompt_templates: dict[str, str] | None = None,
) -> MaterializeResult:
    """Load the saved workflow *name*, compile it, and materialize the run.

    Raises KeyError if no workflow with that name is saved; ValueError if the
    stored definition is invalid or compile/start validation fails.
    """
    # Lazy import keeps this module free of cycles: user_config pulls in config
    # machinery only at call time.
    from murder.user_config import load_workflows  # noqa: PLC0415

    # Last match wins, mirroring save_workflows' "last dupe wins" normalization,
    # so a launch sees the same definition a re-save would persist.
    found: dict | None = None
    for d in load_workflows():
        if d.get("name") == name:
            found = d
    if found is None:
        raise KeyError(name)

    defn = WorkflowDef.model_validate(found)
    return start_workflow_from_def(
        conn,
        repo_root,
        defn,
        args,
        prompt_templates=prompt_templates,
    )


def start_workflow_from_def(
    conn: sqlite3.Connection,
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
        raise ValueError("workflow compile failed: " + "; ".join(messages))

    merged = apply_input_defaults(compiled.inputs, args)
    missing = required_input_issues(compiled.inputs, merged)
    if missing:
        raise ValueError(
            "workflow start blocked: " + "; ".join(issue.message for issue in missing)
        )

    return materialize_workflow(conn, repo_root, compiled.expanded_template, merged)
