"""Plan I/O concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.bus import Entity
from murder.llm.direct import resolve_direct_role_client
from murder.runtime.agents.base import AgentStatus
from murder.runtime.terminal import tmux
from murder.runtime.terminal.session_names import format_session_name
from murder.state.persistence.agents import (
    rename_agent as _db_rename_agent,
    set_agent_status as _db_set_agent_status,
)
from murder.state.persistence.plans import (
    get_plan_row as _db_get_plan_row,
    live_plan_name_exists as _db_live_plan_name_exists,
    rename_plan as _db_rename_plan,
    upsert_plan as _db_upsert_plan,
)
from murder.state.storage.paths import plan_md
from murder.work import notes as notes_mod
from murder.work.plans.parser import (
    render as _render_plan_markdown,
    write as _write_plan_markdown,
)
from murder.work.plans.schema import Plan, PlanStatus
from murder.work.plans.sync import content_hash as _plan_content_hash

LOGGER = logging.getLogger(__name__)

SendAgentMessage = Callable[..., Awaitable[dict[str, Any]]]


def _validate_plan_filename_stem(name: str, *, command: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError(f"{command} name must be a single filename stem")
    return name


def _free_superseded_plan_name(db: Any, name: str) -> str:
    """Release ``name`` from the superseded plan that currently owns it.

    Renames the superseded DB row (PRIMARY KEY ``plans.name``) to a collision-
    safe archived key so a fresh plan can take ``name``. All of the old plan's
    data — body, revisions, related tickets, and its deprecated-dir markdown —
    is preserved; only the DB key changes (its ``materialized_path`` is carried
    through unchanged so the on-disk file is not orphaned). Returns the new key.

    F3b: this is the chosen resolution of the schema/app uniqueness tension —
    free the superseded row's name at create-time (option (a)). No schema change
    or migration, and it reuses the existing data-preserving ``rename_plan``.
    """
    row = _db_get_plan_row(db, name)
    assert row is not None
    materialized_path = str(row.get("materialized_path") or "")
    base = f"{name}-superseded"
    archived = base
    i = 2
    while _db_get_plan_row(db, archived) is not None:
        archived = f"{base}-{i}"
        i += 1
    with db:
        _db_rename_plan(db, name, archived, materialized_path=materialized_path)
    return archived


class PlanOps:
    """Plan scaffold/rename/deprecate/create operations over an ``OrchestratorHost``."""

    def __init__(
        self,
        rt: OrchestratorHost,
        *,
        send_agent_message: SendAgentMessage,
        planner_spawn_locks: dict[str, asyncio.Lock],
    ) -> None:
        self.rt = rt
        self._send_agent_message = send_agent_message
        self._planner_spawn_locks = planner_spawn_locks

    async def scaffold_plan(self, name: str, body: str) -> dict[str, Any]:
        """Create or refresh a draft plan row and its materialized markdown."""
        assert self.rt.db is not None
        name = _validate_plan_filename_stem(name, command="plan.scaffold")
        # Naive UTC (utcnow() is deprecated since 3.12) to match plan timestamps.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        plan = Plan(
            name=name,
            status=PlanStatus.DRAFT,
            created_at=now,
            updated_at=now,
            related_tickets=[],
            frontmatter={},
            body=body,
        )
        path = plan_md(self.rt.repo_root, name)
        materialized_path = str(path.relative_to(self.rt.repo_root))
        rendered = _render_plan_markdown(plan)
        content_hash = _plan_content_hash(rendered)
        with self.rt.db:
            _db_upsert_plan(
                self.rt.db,
                plan,
                content_hash=content_hash,
                materialized_path=materialized_path,
                file_hash=content_hash,
                sync_state="synced",
                create_revision=True,
                revision_source="db",
            )
            _write_plan_markdown(path, plan)
        row = _db_get_plan_row(self.rt.db, name) or {}
        # scaffold_plan writes the plans/plan_revisions rows DIRECTLY (not via
        # PlanSync.reconcile_file), so the on_plan_change callback never fires for
        # it. Emit here: a new/refreshed draft -> the plans list changed. Async
        # path -> await publish_snapshot (closes the 1.5 s poll gap the direct DB
        # write opens before PlanSync would re-reconcile the materialized file).
        await self.rt.publish_snapshot(Entity.PLAN, name)
        return {
            "handled": True,
            "name": name,
            "materialized_path": materialized_path,
            "revision_count": row.get("revision_count"),
        }

    async def rename_plan(self, old_name: str, new_name: str) -> dict[str, Any]:
        """Explicit first-class plan rename with live planner continuity."""
        assert self.rt.db is not None
        old_name = _validate_plan_filename_stem(old_name, command="plan.rename")
        new_name = _validate_plan_filename_stem(new_name, command="plan.rename")
        if old_name == new_name:
            row = _db_get_plan_row(self.rt.db, old_name)
            if row is None:
                raise KeyError(old_name)
            return {
                "handled": True,
                "old_name": old_name,
                "name": new_name,
                "materialized_path": row["materialized_path"],
                "revision_count": row.get("revision_count"),
            }
        if _db_get_plan_row(self.rt.db, old_name) is None:
            raise KeyError(old_name)
        if _db_get_plan_row(self.rt.db, new_name) is not None:
            raise ValueError(f"plan already exists: {new_name}")
        await self._preflight_plan_runtime_rename(old_name, new_name)
        if self.rt.plan_sync is None:
            raise RuntimeError("plan sync not available")
        row = self.rt.plan_sync.rename_plan(old_name, new_name)
        await self._retarget_plan_runtime(old_name, new_name)
        return {
            "handled": True,
            "old_name": old_name,
            "name": new_name,
            "materialized_path": row.get("materialized_path"),
            "revision_count": row.get("revision_count"),
        }

    async def deprecate_plan(self, name: str) -> dict[str, Any]:
        """Mark a plan superseded and remove it from active planning."""
        assert self.rt.db is not None
        name = _validate_plan_filename_stem(name, command="plan.deprecate")
        if self.rt.plan_sync is None:
            raise RuntimeError("plan sync not available")
        row = self.rt.plan_sync.deprecate_plan(name)
        for agent_id in (f"planning_handler-{name}", f"planner-{name}"):
            if self.rt.get_agent(agent_id) is not None:
                await self.rt.reap(agent_id)
            else:
                _db_set_agent_status(self.rt.db, agent_id, AgentStatus.DEAD.value)
        return {
            "handled": True,
            "name": name,
            "status": row.get("status"),
            "materialized_path": row.get("materialized_path"),
            "revision_count": row.get("revision_count"),
        }

    async def _preflight_plan_runtime_rename(self, old_name: str, new_name: str) -> None:
        planner = self.rt.get_agent(f"planner-{old_name}")
        if planner is not None:
            old_session = format_session_name(self.rt, "planner", f"_{old_name}")
            new_session = format_session_name(self.rt, "planner", f"_{new_name}")
            if await tmux.session_exists(old_session) and await tmux.session_exists(
                new_session
            ):
                raise tmux.TmuxError(f"session already exists: {new_session}")
        handler = self.rt.get_agent(f"planning_handler-{old_name}")
        if handler is not None:
            old_session = format_session_name(self.rt, "planning_handler", f"_{old_name}")
            new_session = format_session_name(self.rt, "planning_handler", f"_{new_name}")
            if await tmux.session_exists(old_session) and await tmux.session_exists(
                new_session
            ):
                raise tmux.TmuxError(f"session already exists: {new_session}")

    async def _retarget_plan_runtime(self, old_name: str, new_name: str) -> None:
        assert self.rt.db is not None
        old_lock = self._planner_spawn_locks.pop(old_name, None)
        if old_lock is not None:
            self._planner_spawn_locks[new_name] = old_lock

        old_planner_id = f"planner-{old_name}"
        new_planner_id = f"planner-{new_name}"
        old_planner_session = format_session_name(self.rt, "planner", f"_{old_name}")
        new_planner_session = format_session_name(self.rt, "planner", f"_{new_name}")
        planner = self.rt.rename_agent(old_planner_id, new_planner_id)
        await tmux.rename_session(old_planner_session, new_planner_session)
        if planner is not None:
            planner.session = new_planner_session
            if hasattr(planner, "plan_name"):
                planner.plan_name = new_name
            harness_session = getattr(planner, "harness_session", None)
            if harness_session is not None:
                harness_session.session = new_planner_session

        old_handler_id = f"planning_handler-{old_name}"
        new_handler_id = f"planning_handler-{new_name}"
        old_handler_session = format_session_name(self.rt, "planning_handler", f"_{old_name}")
        new_handler_session = format_session_name(self.rt, "planning_handler", f"_{new_name}")
        handler = self.rt.rename_agent(old_handler_id, new_handler_id)
        await tmux.rename_session(old_handler_session, new_handler_session)
        if handler is not None:
            handler.session = new_handler_session
            if hasattr(handler, "plan_name"):
                handler.plan_name = new_name
            if hasattr(handler, "planner_session"):
                handler.planner_session = new_planner_session

        with self.rt.db:
            _db_rename_agent(
                self.rt.db,
                old_planner_id,
                new_planner_id,
                session=new_planner_session,
            )
            _db_rename_agent(
                self.rt.db,
                old_handler_id,
                new_handler_id,
                session=new_handler_session,
            )
            if planner is not None:
                self.rt.sync_agent(planner)
            if handler is not None:
                self.rt.sync_agent(handler)

    async def _derive_plan_name(self, body: str) -> str:
        """Derive a slugified plan name from ``body`` via a one-shot mini-LLM call.

        Reuses the notes ``llm_capture_metadata`` shape with the ``plan_namer``
        system prompt. Falls back to a timestamp slug when no client is
        configured or the model returns nothing usable.
        """
        text = (body or "").strip()
        client, notetaker_cfg = resolve_direct_role_client(
            self.rt.config.notetaker,
            self.rt.user_cfg,
            "plan_namer",
            "notetaker",
        )
        if client is not None and text:
            try:
                meta = await notes_mod.llm_capture_metadata(
                    raw=text,
                    system=notes_mod._load_prompt("plan_namer"),
                    client=client,
                    config=notetaker_cfg,
                )
                slug = notes_mod._slugify_title(meta.get("one_or_two_word_title", ""))
                if slug:
                    return slug
            except Exception:
                LOGGER.exception("plan auto-name failed; falling back to timestamp slug")
        return f"plan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    async def create_plan(
        self,
        plan_name: str | None,
        message: str,
        *,
        body: str | None = None,
        auto_name: bool = False,
    ) -> dict[str, Any]:
        """Create a new plan and (optionally) seed its planning agent.

        Thin composition of existing machinery: ``scaffold_plan`` writes the
        plan row + materialized markdown (and emits the plan snapshot), then —
        when an initial ``message`` is supplied — ``send_agent_message`` to the
        ``planner-{name}`` agent, which lazily spawns the planner via
        ``ensure_planning_agent``. This is the new-plan flow originally exposed
        via the retired Textual ctrl+p binding (scaffold + focus
        ``planner-{name}`` as chat target).

        ``body`` seeds the plan's markdown body (defaulting to the legacy
        ``"# Plan Name\\n"`` stub). ``auto_name`` derives the plan name from
        ``body`` via a mini-LLM naming call, creating under the FINAL name (no
        rename in the happy path).
        """
        seed_body = body if body is not None else "# Plan Name\n"
        plan_name = (plan_name or "").strip()
        if auto_name:
            plan_name = await self._derive_plan_name(seed_body)
        if not plan_name:
            raise ValueError("plan.create requires plan_name")
        assert self.rt.db is not None
        # Data-integrity guard (F3b): scaffold_plan UPSERTs, so creating over an
        # existing name would silently clobber that plan's body. A *live* plan
        # owns its name — reject and never overwrite. A *superseded* plan does
        # not block reuse, but the plans.name PRIMARY KEY still forbids a second
        # row, so free the superseded row's name first (rename it to an archived
        # key, preserving all its data + its deprecated-dir markdown) before the
        # scaffold INSERT. This keeps the DB constraint and the app guard in
        # exact agreement at INSERT time. Mirrors notes' status-aware guard.
        if _db_live_plan_name_exists(self.rt.db, plan_name):
            raise FileExistsError(
                f"a plan named {plan_name!r} already exists; "
                "choose a different name or rename the existing plan"
            )
        existing = _db_get_plan_row(self.rt.db, plan_name)
        if existing is not None:
            # Superseded row holds the name — archive it (data fully preserved)
            # so the scaffold can take the name. Atomic with no markdown change
            # to the archived plan: rename_plan carries its materialized_path
            # through, so its deprecated-dir file is not orphaned.
            archived = _free_superseded_plan_name(self.rt.db, plan_name)
            await self.rt.publish_snapshot(Entity.PLAN, archived)
        scaffolded = await self.scaffold_plan(plan_name, seed_body)
        name = str(scaffolded.get("name") or plan_name)
        agent_id: str | None = None
        text = (message or "").strip()
        if text:
            agent_id = f"planner-{name}"
            await self._send_agent_message(agent_id, text, None)
        return {"handled": True, "ok": True, "plan_name": name, "agent_id": agent_id}


__all__ = ["PlanOps", "_free_superseded_plan_name", "_validate_plan_filename_stem"]
