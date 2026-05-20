"""Orchestration: spawn/kill agents; wave kickoff; ready computation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder import persistence as dbmod
from murder import notes as notes_mod
from murder import notetaker_capture
from murder.terminal import tmux
from murder.agents.base import AgentRole, AgentStatus
from murder.agents.collaborator import CollaboratorAgent
from murder.agents.crow import CrowAgent
from murder.agents.crow_handler import CrowHandlerAgent
from murder.agents.sentinel import SentinelAgent
from murder.bus import StatusChangeEvent, TicketStatus
from murder.clients import create_client
from murder.config import resolve_default_crow_harness, resolve_default_crow_startup_model
from murder.harnesses import get as get_harness
from murder.prompts import load, render
from murder.session_names import format_session_name
from murder.tickets import carve, lifecycle

from murder_newstructure.service.runtime_scope import OrchestratorHost

from ..escalations.service import EscalationService
from .outcome import TicketOutcomeService

CONFLICT_PREVIEW_LIMIT = 5


def _compose_crow_brief(rt: OrchestratorHost, ticket_id: str) -> str:
    row = dbmod.get_ticket(rt.db, ticket_id)
    if row is None:
        raise KeyError(ticket_id)
    harness_name = resolve_default_crow_harness(rt.config.default_crow, row)
    tpl_name = (
        rt.config.default_crow.startup_prompt_template or f"crow_{harness_name}.md"
    ).removesuffix(".md")
    try:
        system = load(tpl_name)
    except OSError:
        system = load("crow_cursor")
    lines = [
        system,
        "",
        "## Ticket metadata",
        f"- id: {row['id']}",
        f"- title: {row['title']}",
        f"- wave: {row['wave']}",
        f"- harness: {harness_name}",
        "",
        "## Dependencies",
        ", ".join(row.get("deps") or []) or "(none)",
        "",
        "## Write set",
        "\n".join(f"- {p}" for p in (row.get("write_set") or [])) or "(empty)",
        "",
        "## Skills",
        "\n".join(f"- {s}" for s in (row.get("skills") or [])) or "(none)",
        "",
        "## Checklist",
    ]
    for c in row.get("checklist") or []:
        mark = "x" if c.get("done") else " "
        lines.append(f"- [{mark}] {c.get('text', '')}")
    return "\n".join(lines)


def _format_write_set_conflicts(conflicts: list[tuple[str, str, set[str]]]) -> str:
    parts = [
        f"{a}/{b}: {', '.join(sorted(overlap))}"
        for a, b, overlap in conflicts[:CONFLICT_PREVIEW_LIMIT]
    ]
    suffix = (
        f" (+{len(conflicts) - CONFLICT_PREVIEW_LIMIT} more)"
        if len(conflicts) > CONFLICT_PREVIEW_LIMIT
        else ""
    )
    return (
        "Ready tickets have overlapping write_sets; refusing parallel kickoff: "
        + "; ".join(parts)
        + suffix
    )


class Orchestrator:
    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

    def _escalations(self) -> EscalationService:
        assert self.rt.db is not None
        return EscalationService(
            conn=self.rt.db,
            repo_root=self.rt.repo_root,
            bus=self.rt.bus,
            run_id=self.rt.run_id,
            agent_id="orchestrator",
            role=AgentRole.SENTINEL,
        )

    def _outcomes(self) -> TicketOutcomeService:
        assert self.rt.db is not None
        return TicketOutcomeService(
            conn=self.rt.db,
            repo_root=self.rt.repo_root,
            escalations=self._escalations(),
            emit_status=self._emit_ticket_status,
        )

    async def kickoff_ready(self, only: str | None = None) -> list[str]:
        assert self.rt.db is not None and self.rt.bus is not None and self.rt.run_id is not None
        conn = self.rt.db
        ready = dbmod.compute_ready(conn)
        if only is not None:
            if only not in ready:
                return []
            to_start = [only]
        else:
            to_start = list(ready)
        if only is None:
            conflicts = self._ready_write_set_conflicts(to_start)
            if conflicts:
                reason = _format_write_set_conflicts(conflicts)
                await self._escalations().record_kickoff_conflict(reason)
                return []
        kicked: list[str] = []
        for tid in to_start:
            running = conn.execute(
                "SELECT 1 FROM agents WHERE ticket_id = ? AND role IN ('crow','crow_handler') "
                "AND status IN ('running','idle')",
                (tid,),
            ).fetchone()
            if running:
                continue
            prev = lifecycle.transition(conn, tid, TicketStatus.IN_PROGRESS)
            await self._emit_ticket_status(tid, prev, TicketStatus.IN_PROGRESS.value)
            try:
                await self.spawn_crow(tid)
            except Exception as e:
                reason = f"Failed to start crow for {tid}: {e}"
                crow = self.rt.get_crow(tid)
                if crow is not None:
                    crow.status = AgentStatus.FAILED
                    self.rt.sync_agent(crow)
                else:
                    dbmod.upsert_agent(
                        conn,
                        agent_id=f"crow-{tid}",
                        role=AgentRole.CROW.value,
                        ticket_id=tid,
                        session=format_session_name(self.rt, "crow", f"_{tid}"),
                        status=AgentStatus.FAILED.value,
                    )
                await self._fail_ticket(tid, reason)
                continue
            crow = self.rt.get_crow(tid)
            assert crow is not None
            await self.spawn_crow_handler(tid, crow.session)
            kicked.append(tid)
        return kicked

    def _ready_write_set_conflicts(self, ticket_ids: list[str]) -> list[tuple[str, str, set[str]]]:
        assert self.rt.db is not None
        rows = [dbmod.get_ticket(self.rt.db, tid) for tid in ticket_ids]
        tickets = [r for r in rows if r is not None]
        out: list[tuple[str, str, set[str]]] = []
        for i, a in enumerate(tickets):
            for b in tickets[i + 1 :]:
                if a["wave"] != b["wave"]:
                    continue
                overlap = {str(p) for p in a.get("write_set") or []} & {
                    str(p) for p in b.get("write_set") or []
                }
                if overlap:
                    lo, hi = sorted([a["id"], b["id"]])
                    out.append((lo, hi, overlap))
        return sorted(out, key=lambda item: (item[0], item[1]))

    async def _emit_ticket_status(
        self, ticket_id: str, from_status: str | TicketStatus, to_status: str
    ) -> None:
        if self.rt.bus is None or self.rt.run_id is None:
            return
        from_s = from_status.value if isinstance(from_status, TicketStatus) else from_status
        await self.rt.bus.publish(
            StatusChangeEvent(
                run_id=self.rt.run_id,
                agent_id="orchestrator",
                role=AgentRole.SENTINEL,
                ticket_id=ticket_id,
                entity="ticket",
                entity_id=ticket_id,
                from_status=from_s,
                to_status=to_status,
            )
        )

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        await self._outcomes().fail_ticket(ticket_id, reason)

    async def spawn_crow(self, ticket_id: str) -> str:
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        harness = get_harness(harness_kind, startup_model=startup_model)
        session = format_session_name(self.rt, "crow", f"_{ticket_id}")
        brief = _compose_crow_brief(self.rt, ticket_id)
        crow = CrowAgent(
            agent_id=f"crow-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            harness=harness,
            repo_root=self.rt.repo_root,
            startup_model=startup_model,
            runtime=self.rt,
        )
        self.rt.register_agent(crow)
        await crow.start(brief, {})
        return crow.id

    async def spawn_crow_handler(self, ticket_id: str, crow_session: str) -> str:
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        startup_model = resolve_default_crow_startup_model(
            self.rt.config.default_crow, row, harness_kind
        )
        harness = get_harness(harness_kind, startup_model=startup_model)
        session = format_session_name(self.rt, "crow_handler", f"_{ticket_id}")
        client = create_client(self.rt.config.crow_handler.provider)
        crow_agent = self.rt.get_crow(ticket_id)
        start_commit = getattr(crow_agent, "start_commit", None) if crow_agent else None
        handler = CrowHandlerAgent(
            agent_id=f"crow_handler-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            crow_session=crow_session,
            harness=harness,
            config=self.rt.config.crow_handler,
            repo_root=self.rt.repo_root,
            runtime=self.rt,
            outcome=self._outcomes(),
            start_commit=start_commit,
            client=client,
        )
        self.rt.register_agent(handler)
        await handler.start("", {})
        return handler.id

    async def ensure_sentinel(self) -> str:
        assert self.rt.db is not None
        row = self.rt.db.execute(
            "SELECT agent_id FROM agents WHERE role = 'sentinel' "
            "AND status IN ('running','idle') LIMIT 1"
        ).fetchone()
        if row:
            return str(row["agent_id"])
        client = create_client(self.rt.config.sentinel.provider)
        session = format_session_name(self.rt, "sentinel", "")
        agent = SentinelAgent(
            agent_id="sentinel-0",
            session=session,
            config=self.rt.config.sentinel,
            client=client,
            runtime=self.rt,
        )
        self.rt.register_agent(agent)
        await agent.start("", {})
        return agent.id

    async def ensure_collaborator(self) -> str:
        row = self.rt.db.execute(
            "SELECT agent_id FROM agents WHERE role = 'collaborator' "
            "AND status IN ('running','idle') LIMIT 1"
        ).fetchone()
        if row:
            agent_id = str(row["agent_id"])
            agent = self.rt.get_agent(agent_id)
            if agent is not None:
                if await tmux.session_exists(agent.session):
                    return agent_id
                await self.rt.reap(agent_id)
            else:
                dbmod.set_agent_status(self.rt.db, agent_id, "dead")
        startup_model = self.rt.config.collaborator.startup_model
        harness = get_harness(self.rt.config.collaborator.harness, startup_model=startup_model)
        session = format_session_name(self.rt, "collaborator", "")
        agent = CollaboratorAgent(
            agent_id="collaborator-0",
            session=session,
            harness=harness,
            repo_root=self.rt.repo_root,
            startup_model=startup_model,
            runtime=self.rt,
        )
        self.rt.register_agent(agent)
        tpl_raw = self.rt.config.collaborator.startup_prompt_template or "collaborator.md"
        tpl = tpl_raw.removesuffix(".md")
        try:
            body = render(tpl, project_name=self.rt.config.project.name)
        except (KeyError, IndexError, ValueError):
            # Custom template without (or with mismatched) placeholders — use it verbatim.
            body = load(tpl)
        try:
            await agent.start(body, {})
        except Exception as e:
            reason = f"Collaborator startup failed: {e}"
            await self._escalations().record_collaborator_startup_failure(reason)
            await self.rt.reap(agent.id)
            raise
        except BaseException:
            await self.rt.reap(agent.id)
            raise
        return agent.id

    async def submit_notetaker_capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.rt.db is not None

        raw = payload.get("raw")
        if raw is None:
            raw = payload.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                "notetaker.capture.submit requires non-empty payload.raw or payload.text"
            )

        client = create_client(self.rt.config.notetaker.provider)
        return await notetaker_capture.submit_capture(
            repo_root=self.rt.repo_root,
            conn=self.rt.db,
            raw=raw.strip(),
            client=client,
            config=self.rt.config.notetaker,
            note_name=notes_mod.today_name(),
        )

    async def evaluate_wave_completion(self, wave: int) -> bool:
        assert self.rt.db is not None
        tickets = dbmod.list_tickets_in_wave(self.rt.db, wave)
        if not tickets:
            return True
        return all(t["status"] == TicketStatus.DONE.value for t in tickets)

    async def reopen_ticket(self, ticket_id: str) -> list[str]:
        assert self.rt.db is not None
        cascaded = lifecycle.reopen(self.rt.db, ticket_id)
        for tid in {ticket_id, *cascaded}:
            await self.rt.reap(f"crow-{tid}")
            await self.rt.reap(f"crow_handler-{tid}")
        return list(cascaded)

    async def retry_failed_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Transition a failed ticket back to planned and clear its last_error."""
        assert self.rt.db is not None
        prev = lifecycle.transition(self.rt.db, ticket_id, TicketStatus.PLANNED, reason="retry")
        lifecycle.clear_last_error(self.rt.db, ticket_id)
        await self.rt.reap(f"crow-{ticket_id}")
        await self.rt.reap(f"crow_handler-{ticket_id}")
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.PLANNED.value)
        return {"handled": True, "ticket_id": ticket_id, "prev_status": prev.value}

    async def set_schedule_at(self, ticket_id: str, schedule_at: str | None) -> dict[str, Any]:
        """Update the schedule_at timestamp for a ticket."""
        assert self.rt.db is not None
        self.rt.db.execute(
            "UPDATE tickets SET schedule_at = ? WHERE id = ?", (schedule_at, ticket_id)
        )
        self.rt.db.commit()
        return {"handled": True, "ticket_id": ticket_id, "schedule_at": schedule_at}

    async def update_ticket_metadata(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Update metadata fields directly without state-machine transitions."""
        assert self.rt.db is not None
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"handled": True, "ok": False, "error": f"ticket not found: {ticket_id}"}
        title = str(payload.get("title") or row.get("title") or "").strip()
        if not title:
            return {"handled": True, "ok": False, "error": "title is required"}
        wave_raw = payload.get("wave")
        try:
            wave = int(wave_raw) if wave_raw is not None else int(row.get("wave", 0))
        except (TypeError, ValueError):
            return {"handled": True, "ok": False, "error": "wave must be an integer"}
        harness = str(payload.get("harness") or row.get("harness") or "cursor").strip()
        model = payload.get("model") or None
        if model is not None:
            model = str(model).strip() or None
        schedule_at = payload.get("schedule_at")
        if schedule_at is not None:
            schedule_at = str(schedule_at).strip() or None
        deps = [str(d) for d in (payload.get("deps") or [])]
        skills = [str(s) for s in (payload.get("skills") or [])]
        write_set = [str(p) for p in (payload.get("write_set") or [])]
        checklist = [str(c) for c in (payload.get("checklist") or [])]
        with self.rt.db:
            self.rt.db.execute(
                "UPDATE tickets SET wave=?, schedule_at=? WHERE id=?",
                (wave, schedule_at, ticket_id),
            )
            dbmod.apply_ticket_carve_payload(
                self.rt.db,
                ticket_id,
                title=title,
                harness=harness,
                model=model,
                deps=deps,
                skills=skills,
                write_set=write_set,
                checklist=checklist,
            )
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def force_ticket_status(self, ticket_id: str, status: str) -> dict[str, Any]:
        """Force-set ticket status regardless of current state."""
        assert self.rt.db is not None
        valid = {"planned", "ready", "in_progress", "blocked", "failed", "done", "archived"}
        if status not in valid:
            return {"handled": True, "ok": False, "error": f"invalid status: {status!r}"}
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            return {"handled": True, "ok": False, "error": f"ticket not found: {ticket_id}"}
        prev_str = str(row.get("status") or "planned")
        with self.rt.db:
            dbmod.update_ticket_status(self.rt.db, ticket_id, status)
            if prev_str == "failed" and status != "failed":
                lifecycle.clear_last_error(self.rt.db, ticket_id)
        try:
            prev = TicketStatus(prev_str)
        except ValueError:
            prev = TicketStatus.PLANNED
        await self._emit_ticket_status(ticket_id, prev, status)
        return {"handled": True, "ok": True, "ticket_id": ticket_id, "prev_status": prev_str}

    async def on_writeset_violation(self, ticket_id: str, path: str) -> None:
        if self.rt.db is None:
            return
        await self._escalations().block_writeset_violation(ticket_id, path)

    async def on_crow_done(self, ticket_id: str) -> bool:
        assert self.rt.db is not None
        crow = self.rt.get_crow(ticket_id)
        start_commit = getattr(crow, "start_commit", None) if crow else None
        return await self._outcomes().complete_after_crow(
            ticket_id, start_commit=start_commit
        )

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Apply carved sidecar from structured ``carve`` or legacy ``yaml`` string."""
        assert self.rt.db is not None
        carve_body = payload.get("carve")
        yaml_text = payload.get("yaml")
        try:
            if isinstance(carve_body, dict) and carve_body:
                spec = dict(carve_body)
                if spec.get("id") is None:
                    spec["id"] = ticket_id
                prev = carve.ingest_carve_ready_spec(
                    conn=self.rt.db,
                    repo_root=str(self.rt.repo_root),
                    ticket_id=ticket_id,
                    spec=spec,
                )
            elif isinstance(yaml_text, str) and yaml_text.strip():
                spec = carve.parse_carve_yaml(yaml_text)
                prev = carve.ingest_carve_ready_spec(
                    conn=self.rt.db,
                    repo_root=str(self.rt.repo_root),
                    ticket_id=ticket_id,
                    spec=spec,
                )
            else:
                return {
                    "handled": True,
                    "ok": False,
                    "error": "payload must include non-empty 'carve' object or 'yaml' string",
                }
        except carve.CarveError as exc:
            return {"handled": True, "ok": False, "error": str(exc)}
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.READY.value)
        return {"handled": True, "ok": True, "ticket_id": ticket_id}
