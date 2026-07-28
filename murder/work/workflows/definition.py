"""Workflow definition model + pure validation.

Preferred names going forward: ``WorkflowTemplate`` (alias of ``WorkflowDef``)
and ``WorkflowNodeTemplate`` (alias of ``StageDef``). Persisted/API shapes still
use the legacy class names; new code should prefer the aliases.

This is a deep module: the surface is ``WorkflowTemplate`` / ``WorkflowDef`` +
``validate_workflow``, while the dependency-graph reasoning (uniqueness,
dangling refs, cycle detection) stays hidden inside ``validate_workflow``.
Validation is deliberately I/O-free so the storage layer
(``murder.user_config.save_workflows``) and tests can drive it without touching
the filesystem.

Several fields (``gate``, ``mode``) are reserved for the coordination layer that
isn't built yet; only their default value is honored today. They live in the
schema now so persisted definitions don't need a migration when that layer
lands.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from murder.app.protocol.common import ApplicationModel

# A workflow's firing key and each stage's local id share this charset so they're
# safe as YAML keys, ticket-id fragments, and CLI tokens.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StageDef(BaseModel):
    """One agent node (stage) within a workflow template.

    Prefer the ``WorkflowNodeTemplate`` alias in new code. ``instructions`` is
    the brief handed to the agent; it may carry ``{placeholder}`` tokens filled
    in at launch. ``worktree`` is a *named* tree: nodes sharing a name are
    intended to share a checkout, so a later node can build on an earlier one's
    edits.
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
    """A reusable pipeline of stages, keyed by ``name``.

    Prefer the ``WorkflowTemplate`` alias in new code. ``WorkflowDef`` remains
    the concrete class (and the name used in persisted/API payloads) for now.
    """

    name: str  # firing key; ^[A-Za-z0-9_-]+$
    # Definition versions are compatibility boundaries for already-running
    # persisted state machines; changing semantics requires a new version.
    definition_version: int = Field(default=1, ge=1)
    description: str = ""
    # Reserved for generative ticket expansion; only "static" is honored today.
    mode: Literal["static", "generative"] = "static"
    stages: list[StageDef] = Field(default_factory=list)


# Preferred terminology aliases (no behavior / schema change).
WorkflowNodeTemplate = StageDef
WorkflowTemplate = WorkflowDef


WorkflowIssueCode = Literal[
    "invalid_name",
    "no_stages",
    "invalid_stage_id",
    "duplicate_stage_id",
    "missing_harness",
    "missing_model",
    "self_dependency",
    "unknown_dependency",
    "duplicate_dependency",
    "cycle",
    "no_root",
    "unsupported_mode",
    "unsupported_gate",
]


class WorkflowIssue(ApplicationModel):
    """A machine-readable semantic workflow validation finding.

    ``message`` deliberately remains the established public validation text.
    Callers which predate structured issues can therefore continue to use
    :func:`validate_workflow` without a behavior change.
    """

    code: WorkflowIssueCode
    message: str
    path: list[str | int] = Field(default_factory=list)
    stage_id: str | None = None
    dependency_id: str | None = None
    severity: Literal["error", "warning"] = "error"


def validate_workflow(defn: WorkflowDef) -> list[str]:
    """Return human-readable errors for *defn*; empty list means valid.

    Pydantic already guarantees field *shapes*; this checks the cross-field and
    graph invariants it can't express: a usable name, a non-empty stage set with
    unique well-formed ids, dependency references that resolve, no self-edges, an
    acyclic graph, and (for static mode) at least one root to kick off.
    """
    return [issue.message for issue in workflow_issues(defn) if issue.severity == "error"]


def workflow_issues(defn: WorkflowDef) -> list[WorkflowIssue]:  # noqa: PLR0912
    """Return structured semantic validation findings for *defn*.

    This is the authoritative validation boundary for registry mutations.  The
    string-only API above is intentionally a thin compatibility wrapper.
    """
    issues: list[WorkflowIssue] = []

    if not defn.name:
        issues.append(
            WorkflowIssue(code="invalid_name", message="workflow name is empty", path=["name"])
        )
    elif not _NAME_RE.match(defn.name):
        issues.append(
            WorkflowIssue(
                code="invalid_name",
                message=f"workflow name {defn.name!r} must match [A-Za-z0-9_-]+",
                path=["name"],
            )
        )

    if not defn.stages:
        issues.append(
            WorkflowIssue(code="no_stages", message="workflow has no stages", path=["stages"])
        )
        # Without stages the remaining graph checks are vacuous.
        return issues

    if defn.mode != "static":
        issues.append(
            WorkflowIssue(
                code="unsupported_mode",
                message=f"workflow mode {defn.mode!r} is not supported at runtime",
                path=["mode"],
                severity="warning",
            )
        )

    # Build the id set first; later checks reference it. A duplicate id makes the
    # later-listed stage shadow the earlier one in any id->stage map, so we flag
    # duplicates explicitly rather than letting them silently merge.
    seen: set[str] = set()
    ids: set[str] = set()
    for stage_index, stage in enumerate(defn.stages):
        if not _NAME_RE.match(stage.id):
            issues.append(
                WorkflowIssue(
                    code="invalid_stage_id",
                    message=f"stage id {stage.id!r} must match [A-Za-z0-9_-]+",
                    path=["stages", stage_index, "id"],
                    stage_id=stage.id,
                )
            )
        if stage.id in seen:
            issues.append(
                WorkflowIssue(
                    code="duplicate_stage_id",
                    message=f"duplicate stage id {stage.id!r}",
                    path=["stages", stage_index, "id"],
                    stage_id=stage.id,
                )
            )
        seen.add(stage.id)
        ids.add(stage.id)
        # Every stage materializes a *frontmatter* ticket, and the ticket parser
        # requires a non-empty harness+model on any frontmatter ticket. Demanding
        # them here turns that downstream parse error into an actionable, launch-
        # time complaint — and it matches the feature's intent: a stage is a
        # deliberate "this harness, this model" agent invocation.
        if not stage.harness:
            issues.append(
                WorkflowIssue(
                    code="missing_harness",
                    message=f"stage {stage.id!r} requires a harness",
                    path=["stages", stage_index, "harness"],
                    stage_id=stage.id,
                )
            )
        if not stage.model:
            issues.append(
                WorkflowIssue(
                    code="missing_model",
                    message=f"stage {stage.id!r} requires a model",
                    path=["stages", stage_index, "model"],
                    stage_id=stage.id,
                )
            )
        if stage.gate != "auto":
            issues.append(
                WorkflowIssue(
                    code="unsupported_gate",
                    message=f"stage {stage.id!r} gate {stage.gate!r} is not supported at runtime",
                    path=["stages", stage_index, "gate"],
                    stage_id=stage.id,
                    severity="warning",
                )
            )

    for stage_index, stage in enumerate(defn.stages):
        dep_seen: set[str] = set()
        for dependency_index, dep in enumerate(stage.depends_on):
            if dep == stage.id:
                issues.append(
                    WorkflowIssue(
                        code="self_dependency",
                        message=f"stage {stage.id!r} depends on itself",
                        path=["stages", stage_index, "depends_on", dependency_index],
                        stage_id=stage.id,
                        dependency_id=dep,
                    )
                )
            elif dep not in ids:
                issues.append(
                    WorkflowIssue(
                        code="unknown_dependency",
                        message=f"stage {stage.id!r} depends on unknown stage {dep!r}",
                        path=["stages", stage_index, "depends_on", dependency_index],
                        stage_id=stage.id,
                        dependency_id=dep,
                    )
                )
            # A repeated dep is malformed input: it double-counts in the Kahn
            # indegree and writes the same dep id into the stage's frontmatter
            # twice. Reject it rather than silently dedupe so the author fixes
            # the source definition.
            if dep in dep_seen:
                issues.append(
                    WorkflowIssue(
                        code="duplicate_dependency",
                        message=f"stage {stage.id!r} has duplicate dependency {dep!r}",
                        path=["stages", stage_index, "depends_on", dependency_index],
                        stage_id=stage.id,
                        dependency_id=dep,
                    )
                )
            dep_seen.add(dep)

    cycle = _find_cycle(defn)
    if cycle is not None:
        issues.append(
            WorkflowIssue(
                code="cycle",
                message=f"dependency cycle through stage {cycle!r}",
                path=["stages"],
                stage_id=cycle,
            )
        )

    if defn.mode == "static" and not any(not s.depends_on for s in defn.stages):
        issues.append(
            WorkflowIssue(
                code="no_root",
                message="static workflow has no root stage (every stage has dependencies)",
                path=["stages"],
            )
        )

    return issues


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
