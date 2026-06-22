"""Workflow definition model + pure validation.

This is a deep module: the surface is ``WorkflowDef`` + ``validate_workflow``,
while the dependency-graph reasoning (uniqueness, dangling refs, cycle
detection) stays hidden inside ``validate_workflow``. Validation is deliberately
I/O-free so the storage layer (``murder.user_config.save_workflows``) and tests
can drive it without touching the filesystem.

Several fields (``gate``, ``mode``) are reserved for the coordination layer that
isn't built yet; only their default value is honored today. They live in the
schema now so persisted definitions don't need a migration when that layer
lands.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

# A workflow's firing key and each stage's local id share this charset so they're
# safe as YAML keys, ticket-id fragments, and CLI tokens.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StageDef(BaseModel):
    """One agent stage within a workflow.

    ``instructions`` is the brief handed to the agent; it may carry
    ``{placeholder}`` tokens filled in at launch. ``worktree`` is a *named* tree:
    stages sharing a name are intended to share a checkout, so a later stage can
    build on an earlier one's edits.
    """

    id: str  # stage-local; ^[A-Za-z0-9_-]+$, unique within a workflow
    title: str
    instructions: str = ""
    harness: str | None = None
    model: str | None = None
    worktree: str | None = None
    depends_on: list[str] = Field(default_factory=list)  # other stage ids, same workflow
    # Reserved for the coordination layer; only "auto" is honored today.
    gate: Literal["auto", "human", "conditional"] = "auto"


class WorkflowDef(BaseModel):
    """A reusable pipeline of stages, keyed by ``name``."""

    name: str  # firing key; ^[A-Za-z0-9_-]+$
    description: str = ""
    # Reserved for generative ticket expansion; only "static" is honored today.
    mode: Literal["static", "generative"] = "static"
    stages: list[StageDef] = Field(default_factory=list)


def validate_workflow(defn: WorkflowDef) -> list[str]:
    """Return human-readable errors for *defn*; empty list means valid.

    Pydantic already guarantees field *shapes*; this checks the cross-field and
    graph invariants it can't express: a usable name, a non-empty stage set with
    unique well-formed ids, dependency references that resolve, no self-edges, an
    acyclic graph, and (for static mode) at least one root to kick off.
    """
    errors: list[str] = []

    if not defn.name:
        errors.append("workflow name is empty")
    elif not _NAME_RE.match(defn.name):
        errors.append(f"workflow name {defn.name!r} must match [A-Za-z0-9_-]+")

    if not defn.stages:
        errors.append("workflow has no stages")
        # Without stages the remaining graph checks are vacuous.
        return errors

    # Build the id set first; later checks reference it. A duplicate id makes the
    # later-listed stage shadow the earlier one in any id->stage map, so we flag
    # duplicates explicitly rather than letting them silently merge.
    seen: set[str] = set()
    ids: set[str] = set()
    for stage in defn.stages:
        if not _NAME_RE.match(stage.id):
            errors.append(f"stage id {stage.id!r} must match [A-Za-z0-9_-]+")
        if stage.id in seen:
            errors.append(f"duplicate stage id {stage.id!r}")
        seen.add(stage.id)
        ids.add(stage.id)
        # Every stage materializes a *frontmatter* ticket, and the ticket parser
        # requires a non-empty harness+model on any frontmatter ticket. Demanding
        # them here turns that downstream parse error into an actionable, launch-
        # time complaint — and it matches the feature's intent: a stage is a
        # deliberate "this harness, this model" agent invocation.
        if not stage.harness:
            errors.append(f"stage {stage.id!r} requires a harness")
        if not stage.model:
            errors.append(f"stage {stage.id!r} requires a model")

    for stage in defn.stages:
        dep_seen: set[str] = set()
        for dep in stage.depends_on:
            if dep == stage.id:
                errors.append(f"stage {stage.id!r} depends on itself")
            elif dep not in ids:
                errors.append(f"stage {stage.id!r} depends on unknown stage {dep!r}")
            # A repeated dep is malformed input: it double-counts in the Kahn
            # indegree and writes the same dep id into the stage's frontmatter
            # twice. Reject it rather than silently dedupe so the author fixes
            # the source definition.
            if dep in dep_seen:
                errors.append(f"stage {stage.id!r} has duplicate dependency {dep!r}")
            dep_seen.add(dep)

    cycle = _find_cycle(defn)
    if cycle is not None:
        errors.append(f"dependency cycle through stage {cycle!r}")

    if defn.mode == "static" and not any(not s.depends_on for s in defn.stages):
        errors.append("static workflow has no root stage (every stage has dependencies)")

    return errors


def _find_cycle(defn: WorkflowDef) -> str | None:
    """Return a stage id participating in a dependency cycle, or ``None``.

    Iterative DFS with a three-color marking (white=unseen, grey=on the current
    stack, black=fully explored): hitting a grey node means we've looped back
    onto the active path. Self-edges and dangling deps are caught separately, so
    here we only follow deps that resolve to a real stage and skip self-edges.
    """
    adj: dict[str, list[str]] = {}
    for stage in defn.stages:
        # First occurrence wins for a duplicate id; duplicates are already an error.
        adj.setdefault(stage.id, [d for d in stage.depends_on if d != stage.id])

    GREY, BLACK = 1, 2
    color: dict[str, int] = {}

    for root in adj:
        if color.get(root):
            continue
        # Stack of (node, index-into-its-deps) emulating the recursive call frame.
        stack: list[tuple[str, int]] = [(root, 0)]
        color[root] = GREY
        while stack:
            node, i = stack[-1]
            deps = adj.get(node, ())
            if i < len(deps):
                stack[-1] = (node, i + 1)
                nxt = deps[i]
                if nxt not in adj:
                    continue  # dangling dep (flagged elsewhere); not a cycle here
                state = color.get(nxt)
                if state == GREY:
                    return nxt  # back-edge onto the active path
                if state != BLACK:
                    color[nxt] = GREY
                    stack.append((nxt, 0))
            else:
                color[node] = BLACK
                stack.pop()
    return None
