"""Launch a saved or built-in workflow by name into the current project.

The userspace registry (``~/.config/murder/workflows.yaml``) stores reusable
``WorkflowDef`` dumps; launching one resolves it by name (built-ins first) and
hands it to ``materialize_workflow``, which does all the real work (id
allocation, ticket tree, dep wiring). This module is the thin name->definition
lookup that sits in front of that deep module, so the RPC handler stays a shell.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.work.workflows.materialize import MaterializeResult, materialize_workflow


def run_workflow_by_name(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    args: dict[str, str] | None = None,
) -> MaterializeResult:
    """Load workflow *name* (built-in or userspace registry) and materialize it.

    Raises KeyError if no workflow with that name exists; ValueError if the
    stored definition is invalid or launch defaults cannot make it runnable
    (delegated to materialize_workflow / prepare).
    """
    # Lazy imports keep this module free of cycles: user_config pulls in config
    # machinery, and definition is only needed at call time.
    from murder.user_config import load_workflows
    from murder.work.workflows.builtins import (
        get_builtin_workflow,
        prepare_workflow_for_launch,
    )
    from murder.work.workflows.definition import WorkflowDef, validate_workflow

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
    # Built-ins omit harness/model until launch; after prepare they must be concrete.
    errors = validate_workflow(resolved.model_copy(update={"builtin": False}))
    if errors:
        raise ValueError("invalid workflow: " + "; ".join(errors))

    return materialize_workflow(conn, repo_root, resolved, args)
