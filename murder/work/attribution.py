"""Agent file-edit attribution — map a `.murder/` artifact path to its owner.

The single swappable seam (`attribute_edit`) that answers: *which agent wrote
this file?* The current implementation is **convention-based** — ownership is
derived purely from where the file lives and what it's named:

- `.murder/tickets/{id}.md`  → ``crow-{id}``     (the crow owns its ticket)
- `.murder/plans/{name}.md`  → ``planner-{name}`` (the planner owns its plan)

This is intentionally **pure and string-based** (path → id, no DB, no live-agent
lookup) so it is trivially unit-testable and so a future *pane-derived*
implementation (parse tmux panes → general edit→agent log) can replace it
wholesale behind this one function.

Deliberately deferred to that seam swap: a **planner also owns the tickets it
carved**, but a ticket `.md` carries no parent-plan field, so ticket→planner is
not derivable from the path alone. The convention impl therefore attributes a
ticket only to its `crow-{id}`; resolving the carving-planner needs the runtime
knowledge the pane-derived impl is built to supply.
"""

from __future__ import annotations

from pathlib import Path

from murder.state.storage.paths import plans_dir, tickets_dir

__all__ = ["attribute_edit"]


def attribute_edit(path: str | Path, *, repo_root: str | Path) -> str | None:
    """Return the agent id that owns ``path``, or ``None`` if unattributable.

    Pure: depends only on the path and the repo layout, never on DB or live
    agent state. ``repo_root`` anchors the `.murder/` directory the path is
    matched against.
    """
    p = Path(path)
    root = Path(repo_root)

    tickets = tickets_dir(root)
    plans = plans_dir(root)

    if _is_markdown_child_of(p, tickets):
        return f"crow-{p.stem}"
    if _is_markdown_child_of(p, plans):
        # Guard against the deprecated_plans/ subdirectory and any other nested
        # plan artifacts — only a direct `.murder/plans/{name}.md` is owned by a
        # planner agent.
        if p.parent == plans:
            return f"planner-{p.stem}"
    return None


def _is_markdown_child_of(path: Path, directory: Path) -> bool:
    if path.suffix != ".md":
        return False
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return path.parent == directory
