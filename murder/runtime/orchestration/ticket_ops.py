"""Ticket I/O concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from murder.app.service.runtime_scope import OrchestratorHost
from murder.bus import Entity, TicketStatus
from murder.state.persistence.tickets import (
    apply_ticket_carve_payload as _db_apply_ticket_carve_payload,
)
from murder.state.persistence.tickets import (
    get_ticket as _db_get_ticket,
)
from murder.state.persistence.tickets import (
    update_ticket_status as _db_update_ticket_status,
)
from murder.state.storage.paths import ticket_md, tickets_dir
from murder.state.storage.worktrees import prune_terminal_crow_worktree
from murder.work.tickets import carve, lifecycle

LOGGER = logging.getLogger(__name__)

_TNUM_RE = re.compile(r"^t(\d+)$")

EmitTicketStatus = Callable[[str, "str | TicketStatus", str], Awaitable[None]]


class TicketOps:
    """Ticket create/edit/schedule/status operations over an ``OrchestratorHost``."""

    def __init__(
        self,
        rt: OrchestratorHost,
        *,
        emit_ticket_status: EmitTicketStatus,
    ) -> None:
        self.rt = rt
        self._emit_ticket_status = emit_ticket_status

    async def _reap_ticket_crow_agents(self, ticket_id: str) -> None:
        await self.rt.reap(f"crow-{ticket_id}")
        await self.rt.reap(f"crow_handler-{ticket_id}")

    def next_ticket_id(self) -> str:
        """Return the next ``t<NNN>`` id, scanning DB + filesystem for the max.

        Authoritative server-side id allocation; checks both the DB and the
        on-disk ``.md`` files so it stays consistent across the TicketSync poll
        window.
        """
        assert self.rt.db is not None
        conn = self.rt.db
        repo_root = self.rt.repo_root
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
        return f"t{max_n + 1:03d}"

    def ticket_exists(self, handle: str) -> bool:
        """True if ``handle`` names an existing ticket (DB row or on-disk ``.md``)."""
        assert self.rt.db is not None
        handle = handle.strip()
        if not handle:
            return False
        row = self.rt.db.execute(
            "SELECT 1 FROM tickets WHERE id = ?", (handle,)
        ).fetchone()
        if row is not None:
            return True
        return ticket_md(self.rt.repo_root, handle).exists()

    def quick_create_ticket(self, title: str) -> dict[str, Any]:
        """Create a ticket .md + insert it as PLANNED, without kicking it.

        Server-side id allocation + file write + DB insert — the authority the
        TUI's old direct ``.md`` write bypassed (V1).
        """
        assert self.rt.db is not None
        conn = self.rt.db
        repo_root = self.rt.repo_root
        ticket_id = self.next_ticket_id()

        # Write the markdown file so the ticket sync stays consistent.
        path = ticket_md(repo_root, ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n## Plan\n\n## Working Notes\n")

        # Insert directly into DB — bypasses the 1.5 s TicketSync poll.
        from murder.state.persistence.tickets import insert_ticket as _db_insert_ticket
        from murder.work.tickets.schema import Ticket
        from murder.work.tickets.status import TicketStatus

        now = datetime.utcnow().replace(microsecond=0)
        row_existing = conn.execute(
            "SELECT id FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if row_existing is None:
            ticket = Ticket(
                id=ticket_id,
                title=title,
                status=TicketStatus.PLANNED,
                created_at=now,
                updated_at=now,
            )
            try:
                _db_insert_ticket(conn, ticket)
            except Exception as exc:
                # TicketSync may have raced us between the SELECT and INSERT.
                LOGGER.debug(
                    "quick_create_ticket insert raced TicketSync for %s: %s",
                    ticket_id,
                    exc,
                )
        # Sync method (no surrounding coroutine): use the sync emit_snapshot
        # choke point. New ticket -> the schedule snapshot's planned bucket
        # changed. (TicketSync would also emit on its next reconcile, but this
        # closes the 1.5 s poll gap the direct DB insert opens.)
        self.rt.emit_snapshot(Entity.TICKET, ticket_id)
        return {"handled": True, "ticket_id": ticket_id, "title": title}

    async def reopen_ticket(self, ticket_id: str) -> list[str]:
        assert self.rt.db is not None
        cascaded = lifecycle.reopen(self.rt.db, ticket_id)
        for tid in {ticket_id, *cascaded}:
            await self._reap_ticket_crow_agents(tid)
            # F1: reopen cascade has no StatusChangeEvent today; emit the
            # key-only ticket snapshot for every ticket whose status changed.
            await self.rt.publish_snapshot(Entity.TICKET, tid)
        return list(cascaded)

    async def retry_failed_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Transition a failed ticket back to ready and clear its last_error."""
        assert self.rt.db is not None
        prev = lifecycle.transition(self.rt.db, ticket_id, TicketStatus.READY, reason="retry")
        lifecycle.clear_last_error(self.rt.db, ticket_id)
        await self._reap_ticket_crow_agents(ticket_id)
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.READY.value)
        return {"handled": True, "ticket_id": ticket_id, "prev_status": prev.value}

    async def set_schedule_at(self, ticket_id: str, schedule_at: str | None) -> dict[str, Any]:
        """Update the schedule_at timestamp for a ticket."""
        assert self.rt.db is not None
        now = datetime.now().isoformat(timespec="seconds")
        self.rt.db.execute(
            "UPDATE tickets SET schedule_at = ?, updated_at = ? WHERE id = ?",
            (schedule_at, now, ticket_id),
        )
        self.rt.db.commit()
        await self.rt.publish_snapshot(Entity.TICKET, ticket_id)
        return {"handled": True, "ticket_id": ticket_id, "schedule_at": schedule_at}

    async def save_ticket_body(self, ticket_id: str, body: str) -> dict[str, Any]:
        """Persist the edited markdown body for a ticket (the editor's save).

        The Ink ticket editor sends only the markdown *body* (everything after
        the frontmatter: ``## Plan`` / ``## Working Notes`` prose and the
        ``# Checklist`` section). The frontmatter (title/deps/harness/model/
        worktree) is read-only in the editor and absent from the payload, so we
        re-attach the *current* frontmatter rather than wiping it. We write the
        ``.md`` file (the authoritative ticket writer is the filesystem->DB
        reconcile path) then reconcile it synchronously into the DB so the save
        is durable before the RPC returns, closing the 1.5 s TicketSync poll gap.
        """
        assert self.rt.db is not None
        from murder.work.tickets.parser import parse_ticket
        from murder.work.tickets.render import render_ticket_frontmatter
        from murder.work.tickets.sync import reconcile_ticket_md

        ticket_id = ticket_id.strip()
        if not ticket_id:
            raise ValueError("ticket.save_body requires ticket_id")
        path = ticket_md(self.rt.repo_root, ticket_id)
        # Source the read-only frontmatter from the current file when present,
        # else fall back to the DB row so we never drop metadata on save.
        if path.exists():
            frontmatter = render_ticket_frontmatter(
                parse_ticket(path.read_text(encoding="utf-8"), default_title=ticket_id)
            )
        else:
            row = _db_get_ticket(self.rt.db, ticket_id)
            if row is None:
                return {"ok": False, "error": f"ticket not found: {ticket_id}"}
            deps = [
                str(r["depends_on_id"])
                for r in self.rt.db.execute(
                    "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ? "
                    "ORDER BY depends_on_id",
                    (ticket_id,),
                ).fetchall()
            ]
            frontmatter = render_ticket_frontmatter(
                {
                    "title": row.get("title") or ticket_id,
                    "deps": deps,
                    "harness": row.get("harness"),
                    "model": row.get("model"),
                    "worktree": row.get("worktree"),
                }
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frontmatter + body.rstrip("\n") + "\n", encoding="utf-8")
        reconcile_ticket_md(conn=self.rt.db, repo_root=self.rt.repo_root, ticket_id=ticket_id)
        # ``reconcile_ticket_md`` builds a throwaway TicketSync with no
        # on_ticket_change callback, so the F1 snapshot does not fire from it.
        # Emit explicitly after the reconcile commits.
        await self.rt.publish_snapshot(Entity.TICKET, ticket_id)
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def schedule_ticket(self, ticket_id: str, duration: str) -> dict[str, Any]:
        """Set/clear a ticket's schedule from a free-form duration string.

        The Ink editor sends a raw duration (``1d4h3m``, ``34m``); the backend is
        authoritative. An empty/whitespace duration clears the schedule; any
        non-empty value is parsed via ``parse_duration`` and added to *now*.
        Delegates the DB write + snapshot emit to ``set_schedule_at``.

        Stores a UTC timestamp (``utcnow``), matching the rest of the codebase's
        persisted clock (``scaffold_plan``, ``TicketSync._now``, the schedule
        read model) so the scheduler/calendar consumers — which compute "now" in
        UTC — see the intended offset rather than one skewed by the local tz.
        """
        from murder.work.duration import parse_duration

        ticket_id = ticket_id.strip()
        if not ticket_id:
            raise ValueError("ticket.schedule requires ticket_id")
        text = (duration or "").strip()
        if not text:
            return await self.set_schedule_at(ticket_id, None)
        delta = parse_duration(text)
        schedule_at = (datetime.utcnow() + delta).isoformat(timespec="seconds")
        return await self.set_schedule_at(ticket_id, schedule_at)

    async def update_ticket_metadata(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update metadata fields directly without state-machine transitions."""
        assert self.rt.db is not None
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"ok": False, "error": f"ticket not found: {ticket_id}"}
        title = str(payload.get("title") or row.get("title") or "").strip()
        if not title:
            return {"ok": False, "error": "title is required"}
        harness = str(payload.get("harness") or row.get("harness") or "cursor").strip()
        model = payload.get("model") or None
        if model is not None:
            model = str(model).strip() or None
        schedule_at = payload.get("schedule_at")
        if schedule_at is not None:
            schedule_at = str(schedule_at).strip() or None
        deps = [str(d) for d in (payload.get("deps") or [])]
        if "skills" in payload:
            skills = [str(s) for s in (payload.get("skills") or [])]
        else:
            skills = [str(s) for s in (row.get("skills") or [])]
        checklist = [str(c) for c in (payload.get("checklist") or [])]
        with self.rt.db:
            self.rt.db.execute(
                "UPDATE tickets SET schedule_at=? WHERE id=?",
                (schedule_at, ticket_id),
            )
            _db_apply_ticket_carve_payload(
                self.rt.db,
                ticket_id,
                title=title,
                harness=harness,
                model=model,
                deps=deps,
                skills=skills,
                checklist=checklist,
            )
        await self.rt.publish_snapshot(Entity.TICKET, ticket_id)
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def force_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        """Force-set ticket status regardless of current state."""
        assert self.rt.db is not None
        valid = {"planned", "ready", "in_progress", "blocked", "failed", "done", "archived"}
        if status not in valid:
            return {"ok": False, "error": f"invalid status: {status!r}"}
        row = _db_get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"ok": False, "error": f"ticket not found: {ticket_id}"}
        prev_str = str(row.get("status") or "planned")
        with self.rt.db:
            _db_update_ticket_status(self.rt.db, ticket_id, status)
            if prev_str == "failed" and status != "failed":
                lifecycle.clear_last_error(self.rt.db, ticket_id)
        try:
            prev = TicketStatus(prev_str)
        except ValueError:
            prev = TicketStatus.PLANNED
        await self._emit_ticket_status(ticket_id, prev, status)
        if status in (
            TicketStatus.DONE.value,
            TicketStatus.FAILED.value,
            TicketStatus.ARCHIVED.value,
        ):
            await self._reap_ticket_crow_agents(ticket_id)
            if self.rt.db is not None:
                with contextlib.suppress(Exception):
                    await prune_terminal_crow_worktree(
                        self.rt.db, self.rt.repo_root, ticket_id
                    )
        return {"handled": True, "ok": True, "ticket_id": ticket_id, "prev_status": prev_str}

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Apply carved ticket metadata from a structured ``carve`` payload."""
        assert self.rt.db is not None
        carve_body = payload.get("carve")
        try:
            if isinstance(carve_body, dict) and carve_body:
                spec = dict(carve_body)
                if spec.get("id") is None:
                    spec["id"] = ticket_id
                prev = carve.apply_carve_ready_spec(self.rt.db, ticket_id, spec)
            else:
                return {
                    "ok": False,
                    "error": "payload must include non-empty 'carve' object",
                }
        except carve.CarveError as exc:
            return {"ok": False, "error": str(exc)}
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.READY.value)
        return {"handled": True, "ok": True, "ticket_id": ticket_id}


__all__ = ["TicketOps"]
