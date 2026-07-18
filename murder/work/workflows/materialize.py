"""Materialize a workflow definition into a tree of project tickets.

Launching a workflow turns a reusable ``WorkflowDef`` into rows in the current
project's ``state.db``: one ``planned`` parent "run" ticket plus one ``ready``
ticket per stage, wired by dependencies. No new engine runs the pipeline — the
*existing* scheduler does. ``compute_ready`` already gates a ``ready`` ticket on
its deps being ``done``/``archived``, so marking *every* stage ``ready`` (even
downstream ones) lets the scheduler spawn a crow per stage exactly when its
upstream finishes. The parent stays ``planned`` forever so the scheduler never
spawns a crow for the container itself.

This is a deep module: callers hand it ``(defn, args)`` and get back a tree of
ticket ids. The id allocation, topological ordering, ``.md`` rendering,
filesystem->DB reconcile, and planned->ready transitions all stay hidden here.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from murder.state.persistence.workflow_runs import create_workflow_run
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.paths import ticket_md, tickets_dir
from murder.work.tickets import parser
from murder.work.tickets.lifecycle import transition
from murder.work.tickets.render import render_ticket_frontmatter
from murder.work.tickets.status import TicketStatus
from murder.work.tickets.sync import reconcile_ticket_md
from murder.work.workflows.definition import StageDef, WorkflowDef, validate_workflow
from murder.work.workflows.runtime import (
    Correlation,
    ExternalSignalWait,
    PrincipalKind,
    PrincipalRef,
    StageRunState,
    StageStatus,
    StaticDagWorkflowStateV1,
    WorkflowRunRecord,
    WorkflowStatus,
    versioned_state,
)

# Keep the patch point used by older tests/extensions while changing the
# function's payload from ticket-derived columns to an authoritative record.
insert_workflow_run = create_workflow_run

_TNUM_RE = re.compile(r"^t(\d+)$")
# Named ``{placeholder}`` tokens; unknown keys are left verbatim so an
# unfilled placeholder survives into the crow's brief rather than vanishing.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_-]+)\}")


@dataclass(frozen=True)
class MaterializeResult:
    workflow_id: UUID
    run_ticket_id: str
    stage_ticket_ids: dict[str, str]  # stage.id -> ticket id
    created_ticket_ids: list[str]  # parent + all stages, in creation order


def materialize_workflow(
    conn: sqlite3.Connection,
    repo_root: Path,
    defn: WorkflowDef,
    args: dict[str, str] | None = None,
    *,
    now: str | None = None,
) -> MaterializeResult:
    """Materialize *defn* into a ticket tree; return the created ids.

    Raises ``ValueError`` if the definition is invalid (so a bad workflow never
    leaves a partial tree behind).
    """
    errors = validate_workflow(defn)
    if errors:
        raise ValueError("invalid workflow: " + "; ".join(errors))

    created_at = now or _now()
    workflow_now = _workflow_time(created_at)
    repo_root = Path(repo_root)
    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)

    ordered_stages = _topo_sorted(defn)

    # Allocate every id from ONE scan: next_ticket_id-style max-scan returns the
    # same id until a row/file exists, so calling it per ticket would collide.
    # Parent first, then stages in topological order.
    ids = _allocate_ids(conn, repo_root, count=1 + len(ordered_stages))
    run_ticket_id = ids[0]
    stage_ticket_ids: dict[str, str] = {
        stage.id: ticket_id for stage, ticket_id in zip(ordered_stages, ids[1:], strict=True)
    }

    created: list[str] = []

    # ``reconcile_ticket_md`` commits each ticket in its own transaction, so the
    # whole materialize cannot be one atomic DB write without changing the sync
    # commit model. Instead we make failure self-cleaning: if any step raises
    # (mid-loop, or in ``insert_workflow_run``), tear down the tickets created so
    # far so the DB is never left with orphan stage rows and no ``workflow_runs``
    # anchor. ``insert_workflow_run`` runs last so a run record only exists once
    # its full tree does.
    try:
        # Parent "run" ticket: a frontmatter-less container (like a planner file)
        # so reconcile lands it ``planned`` with no required harness/model. It is
        # never transitioned to ``ready``, so the scheduler ignores it.
        _write_run_ticket(repo_root, run_ticket_id, defn, args)
        reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id=run_ticket_id)
        created.append(run_ticket_id)

        # Stages in topological order: a stage's dependency tickets must already
        # exist before we write its ``ticket_deps`` rows (FK constraint). Each
        # stage is reconciled to ``planned`` then transitioned to ``ready``.
        for stage in ordered_stages:
            ticket_id = stage_ticket_ids[stage.id]
            dep_ticket_ids = [stage_ticket_ids[d] for d in stage.depends_on]
            _write_stage_ticket(
                repo_root,
                ticket_id,
                stage,
                parent_ticket_id=run_ticket_id,
                dep_ticket_ids=dep_ticket_ids,
                args=args,
            )
            reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id=ticket_id)
            transition(conn, ticket_id, cast(TicketStatus, TicketStatus.READY))
            created.append(ticket_id)

        workflow_id = uuid4()
        initial_state = StaticDagWorkflowStateV1(
            inputs=dict(args or {}),
            stages=tuple(
                StageRunState(
                    stage_id=stage.id,
                    status=(StageStatus.READY if not stage.depends_on else StageStatus.BLOCKED),
                )
                for stage in defn.stages
            ),
        )
        initial_waits = tuple(
            ExternalSignalWait(
                signal_name="ticket.finished",
                correlation_key=stage_ticket_ids[stage.id],
            )
            for stage in defn.stages
        )
        insert_workflow_run(
            conn,
            WorkflowRunRecord(
                workflow_id=workflow_id,
                definition_name=defn.name,
                definition_version=defn.definition_version,
                status=WorkflowStatus.WAITING,
                revision=0,
                state=versioned_state(
                    initial_state,
                    schema_name="static_dag",
                    schema_version=1,
                ),
                created_at=workflow_now,
                updated_at=workflow_now,
                started_by=PrincipalRef(
                    kind=PrincipalKind.SERVICE,
                    id="ticket-dag-launcher",
                ),
                correlation=Correlation(correlation_id=uuid4()),
                parent_ticket_id=run_ticket_id,
                definition_snapshot=defn.model_dump(mode="json"),
                stage_map=stage_ticket_ids,
            ),
            waits=initial_waits,
        )
    except Exception:
        _cleanup_partial(conn, repo_root, created)
        raise

    return MaterializeResult(
        workflow_id=workflow_id,
        run_ticket_id=run_ticket_id,
        stage_ticket_ids=stage_ticket_ids,
        created_ticket_ids=created,
    )


def _fill(text: str, args: dict[str, str] | None) -> str:
    """Substitute ``{key}`` tokens from *args*; leave unknown tokens verbatim."""
    if not args:
        return text
    return _PLACEHOLDER_RE.sub(lambda m: args.get(m.group(1), m.group(0)), text)


def _topo_sorted(defn: WorkflowDef) -> list[StageDef]:
    """Return stages so every stage follows its dependencies.

    ``validate_workflow`` already guarantees a DAG with resolvable deps, so a
    Kahn-style sweep terminates. Ties break on a stable (definition) order.
    """
    by_id = {stage.id: stage for stage in defn.stages}
    indegree = {stage.id: len(stage.depends_on) for stage in defn.stages}
    # Definition order is the stable tiebreaker for deterministic ids.
    ready = [stage.id for stage in defn.stages if indegree[stage.id] == 0]
    dependents: dict[str, list[str]] = {stage.id: [] for stage in defn.stages}
    for stage in defn.stages:
        for dep in stage.depends_on:
            dependents[dep].append(stage.id)

    out: list[StageDef] = []
    while ready:
        sid = ready.pop(0)
        out.append(by_id[sid])
        for child in dependents[sid]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return out


def _cleanup_partial(conn: sqlite3.Connection, repo_root: Path, created: list[str]) -> None:
    """Best-effort teardown of a half-built workflow tree.

    Each ticket was committed in its own transaction by ``reconcile_ticket_md``,
    so on a mid-materialize failure we undo them explicitly: drop the per-ticket
    dependency rows and ticket rows we created, plus any stray ``workflow_runs``
    anchor, and unlink the ``.md`` files. Children are removed before parents so
    no FK constraint blocks the delete. Failures here are swallowed — the
    original error is what the caller must see.
    """
    for ticket_id in reversed(created):
        try:
            conn.execute(
                "DELETE FROM ticket_deps WHERE ticket_id = ? OR depends_on_id = ?",
                (ticket_id, ticket_id),
            )
            conn.execute("DELETE FROM workflow_runs WHERE parent_ticket_id = ?", (ticket_id,))
            conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
            conn.commit()
        except Exception:
            pass
        try:
            ticket_md(repo_root, ticket_id).unlink(missing_ok=True)
        except OSError:
            pass


def _allocate_ids(conn: sqlite3.Connection, repo_root: Path, *, count: int) -> list[str]:
    """Allocate *count* sequential ``t<NNN>`` ids from one DB + filesystem scan.

    Mirrors ``TicketOps.next_ticket_id`` but hands out a contiguous block in a
    single pass — repeatedly calling a max-scan allocator returns the same id
    until the prior row/file lands, which a synchronous batch never does.
    """
    max_n = 0
    for row in conn.execute("SELECT id FROM tickets WHERE id LIKE 't%'").fetchall():
        m = _TNUM_RE.match(str(row["id"]))
        if m:
            max_n = max(max_n, int(m.group(1)))
    root = tickets_dir(repo_root)
    if root.exists():
        for p in root.glob("*.md"):
            m2 = _TNUM_RE.match(p.stem)
            if m2:
                max_n = max(max_n, int(m2.group(1)))
    return [f"t{max_n + i:03d}" for i in range(1, count + 1)]


def _write_run_ticket(
    repo_root: Path,
    ticket_id: str,
    defn: WorkflowDef,
    args: dict[str, str] | None,
) -> None:
    """Write the parent run ticket's ``.md`` (frontmatter-less container).

    The title rides the leading ``# {title}`` heading (recovered by the parser),
    so no frontmatter block is needed — and thus no harness/model are required
    for it to reconcile to ``planned``.
    """
    plan_lines = [f"Workflow run: {defn.name}"]
    if defn.description:
        plan_lines.append("")
        plan_lines.append(defn.description)
    if args:
        plan_lines.append("")
        plan_lines.append("Arguments:")
        for key in sorted(args):
            plan_lines.append(f"- {key}: {args[key]}")
    body = parser.render(plan="\n".join(plan_lines))
    # The parser recovers a frontmatter-less ticket's title verbatim from this
    # leading heading, so the heading text *is* the stored title. Keep it clean
    # ("Workflow: <name>", no machine prefix) rather than letting a "# workflow:"
    # token leak into ``tickets.title`` and the UI.
    text = f"# Workflow: {defn.name}\n\n{body}"
    _atomic_ticket_write(repo_root, ticket_id, text)


def _write_stage_ticket(
    repo_root: Path,
    ticket_id: str,
    stage: StageDef,
    *,
    parent_ticket_id: str,
    dep_ticket_ids: list[str],
    args: dict[str, str] | None,
) -> None:
    """Write a stage ticket's ``.md``: frontmatter + ``## Plan`` instructions.

    The frontmatter carries everything reconcile populates (title, deps mapped
    to ticket ids, harness, model, worktree, parent); the crow reads its brief
    from the ``## Plan`` section. Rendering through the shared helpers keeps the
    write->reconcile round-trip parse-error-free.
    """
    frontmatter = render_ticket_frontmatter(
        {
            "title": _fill(stage.title, args),
            "deps": dep_ticket_ids,
            "harness": stage.harness,
            "model": stage.model,
            "worktree": stage.worktree,
            "parent": parent_ticket_id,
        }
    )
    body = parser.render(plan=_fill(stage.instructions, args))
    _atomic_ticket_write(repo_root, ticket_id, frontmatter + body)


def _atomic_ticket_write(repo_root: Path, ticket_id: str, text: str) -> None:
    atomic_write_text(ticket_md(repo_root, ticket_id), text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _workflow_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
