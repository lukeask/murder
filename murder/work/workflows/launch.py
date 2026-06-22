"""Launch a saved workflow by name into the current project.

The userspace registry (``~/.config/murder/workflows.yaml``) stores reusable
``WorkflowDef`` dumps; launching one resolves it by name and hands it to
``materialize_workflow``, which does all the real work (id allocation, ticket
tree, dep wiring). This module is the thin name->definition lookup that sits in
front of that deep module, so the RPC handler stays a shell.
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
    """Load the saved workflow *name* from the userspace registry and materialize it.

    Raises KeyError if no workflow with that name is saved; ValueError if the
    stored definition is invalid (delegated to materialize_workflow).
    """
    # Lazy imports keep this module free of cycles: user_config pulls in config
    # machinery, and definition is only needed at call time.
    from murder.user_config import load_workflows
    from murder.work.workflows.definition import WorkflowDef

    # Last match wins, mirroring save_workflows' "last dupe wins" normalization,
    # so a launch sees the same definition a re-save would persist.
    found: dict | None = None
    for d in load_workflows():
        if d.get("name") == name:
            found = d
    if found is None:
        raise KeyError(name)

    defn = WorkflowDef.model_validate(found)
    return materialize_workflow(conn, repo_root, defn, args)
