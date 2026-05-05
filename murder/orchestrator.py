"""Orchestration: spawn/kill agents; wave kickoff; ready computation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from murder import db as dbmod
from murder.agents.augur import AugurAgent
from murder.agents.base import AgentRole, AgentStatus
from murder.agents.collaborator import CollaboratorAgent
from murder.agents.monkey import MonkeyAgent
from murder.agents.sentinel import SentinelAgent
from murder.bus import EscalationEvent, StatusChangeEvent, TicketStatus
from murder.clients import create_client
from murder.config import resolve_default_monkey_harness, resolve_default_monkey_startup_model
from murder.enforcement import git_diff
from murder.enforcement.checklist_verify import format_report, verify_checklist
from murder.harnesses import get as get_harness
from murder.prompts import load
from murder.tickets import lifecycle

if TYPE_CHECKING:
    from murder.runtime import Runtime

CONFLICT_PREVIEW_LIMIT = 5


def _session_name(rt: Runtime, role: str, suffix: str) -> str:
    proj = rt.config.project.name.replace(" ", "_").replace("/", "_")
    tpl = rt.config.runtime.session_name_template
    return tpl.format(project=proj, role=role, suffix=suffix)


def _compose_monkey_brief(rt: Runtime, ticket_id: str) -> str:
    row = dbmod.get_ticket(rt.db, ticket_id)
    if row is None:
        raise KeyError(ticket_id)
    harness_name = resolve_default_monkey_harness(rt.config.default_monkey, row)
    tpl_name = (
        rt.config.default_monkey.startup_prompt_template or f"monkey_{harness_name}.md"
    ).removesuffix(".md")
    try:
        system = load(tpl_name)
    except OSError:
        system = load("monkey_cursor")
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


def _missing_write_set_paths(repo_root: Path, paths: list[Path]) -> list[str]:
    missing: list[str] = []
    for path in paths:
        target = (repo_root / path).resolve()
        if not target.exists():
            missing.append(f"{path} (missing)")
        elif target.is_file() and target.stat().st_size == 0:
            missing.append(f"{path} (empty)")
        elif target.is_dir() and not any(target.iterdir()):
            missing.append(f"{path} (empty directory)")
    return missing


class Orchestrator:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

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
                dbmod.insert_escalation(
                    conn,
                    ticket_id=None,
                    severity=2,
                    reason=reason,
                    to_recipient="user",
                )
                await self._emit_escalation(None, reason, severity=2)
                return []
        kicked: list[str] = []
        for tid in to_start:
            running = conn.execute(
                "SELECT 1 FROM agents WHERE ticket_id = ? AND role IN ('monkey','augur') "
                "AND status IN ('running','idle')",
                (tid,),
            ).fetchone()
            if running:
                continue
            st = dbmod.get_ticket_status(conn, tid)
            if st == TicketStatus.PLANNED.value:
                prev = lifecycle.transition(conn, tid, TicketStatus.READY)
                await self._emit_ticket_status(tid, prev, TicketStatus.READY.value)
            prev = lifecycle.transition(conn, tid, TicketStatus.IN_PROGRESS)
            await self._emit_ticket_status(tid, prev, TicketStatus.IN_PROGRESS.value)
            try:
                await self.spawn_monkey(tid)
            except Exception as e:
                reason = f"Failed to start monkey for {tid}: {e}"
                monkey = self.rt.get_monkey(tid)
                if monkey is not None:
                    monkey.status = AgentStatus.FAILED
                    self.rt.sync_agent(monkey)
                else:
                    dbmod.upsert_agent(
                        conn,
                        agent_id=f"monkey-{tid}",
                        role=AgentRole.MONKEY.value,
                        ticket_id=tid,
                        session=_session_name(self.rt, "monkey", f"_{tid}"),
                        status=AgentStatus.FAILED.value,
                    )
                await self._fail_ticket(tid, reason)
                continue
            monkey = self.rt.get_monkey(tid)
            assert monkey is not None
            await self.spawn_augur(tid, monkey.session)
            kicked.append(tid)
        return kicked

    def _ready_write_set_conflicts(
        self, ticket_ids: list[str]
    ) -> list[tuple[str, str, set[str]]]:
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
        self, ticket_id: str, from_status: str, to_status: str
    ) -> None:
        if self.rt.bus is None or self.rt.run_id is None:
            return
        await self.rt.bus.publish(
            StatusChangeEvent(
                run_id=self.rt.run_id,
                agent_id="orchestrator",
                role=AgentRole.SENTINEL,
                ticket_id=ticket_id,
                entity="ticket",
                entity_id=ticket_id,
                from_status=from_status,
                to_status=to_status,
            )
        )

    async def _emit_escalation(
        self, ticket_id: str | None, reason: str, *, severity: int = 2
    ) -> None:
        if self.rt.bus is None or self.rt.run_id is None:
            return
        await self.rt.bus.publish(
            EscalationEvent(
                run_id=self.rt.run_id,
                agent_id="orchestrator",
                role=AgentRole.SENTINEL,
                ticket_id=ticket_id,
                to="user",
                reason=reason,
                severity=severity,  # type: ignore[arg-type]
            )
        )

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        assert self.rt.db is not None
        old = dbmod.get_ticket_status(self.rt.db, ticket_id)
        prev = TicketStatus(old) if old else TicketStatus.IN_PROGRESS
        try:
            prev = lifecycle.transition(self.rt.db, ticket_id, TicketStatus.FAILED)
        except Exception:
            dbmod.update_ticket_status(self.rt.db, ticket_id, TicketStatus.FAILED.value)
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.FAILED.value)
        dbmod.insert_escalation(
            self.rt.db,
            ticket_id=ticket_id,
            severity=2,
            reason=reason,
            to_recipient="user",
        )
        await self._emit_escalation(ticket_id, reason, severity=2)

    async def spawn_monkey(self, ticket_id: str) -> str:
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_monkey_harness(self.rt.config.default_monkey, row)
        startup_model = resolve_default_monkey_startup_model(
            self.rt.config.default_monkey, row, harness_kind
        )
        harness = get_harness(harness_kind, startup_model=startup_model)
        session = _session_name(self.rt, "monkey", f"_{ticket_id}")
        brief = _compose_monkey_brief(self.rt, ticket_id)
        monkey = MonkeyAgent(
            agent_id=f"monkey-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            harness=harness,
            repo_root=self.rt.repo_root,
            startup_model=startup_model,
            runtime=self.rt,
        )
        self.rt.register_agent(monkey)
        await monkey.start(brief, {})
        return monkey.id

    async def spawn_augur(self, ticket_id: str, monkey_session: str) -> str:
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            raise KeyError(ticket_id)
        harness_kind = resolve_default_monkey_harness(self.rt.config.default_monkey, row)
        startup_model = resolve_default_monkey_startup_model(
            self.rt.config.default_monkey, row, harness_kind
        )
        harness = get_harness(harness_kind, startup_model=startup_model)
        session = _session_name(self.rt, "augur", f"_{ticket_id}")
        client = create_client(self.rt.config.augur.provider)
        augur = AugurAgent(
            agent_id=f"augur-{ticket_id}",
            ticket_id=ticket_id,
            session=session,
            monkey_session=monkey_session,
            harness=harness,
            config=self.rt.config.augur,
            repo_root=self.rt.repo_root,
            runtime=self.rt,
            orchestrator=self,
            client=client,
        )
        self.rt.register_agent(augur)
        await augur.start("", {})
        return augur.id

    async def ensure_sentinel(self) -> str:
        assert self.rt.db is not None
        row = self.rt.db.execute(
            "SELECT agent_id FROM agents WHERE role = 'sentinel' "
            "AND status IN ('running','idle') LIMIT 1"
        ).fetchone()
        if row:
            return str(row["agent_id"])
        client = create_client(self.rt.config.sentinel.provider)
        session = _session_name(self.rt, "sentinel", "")
        agent = SentinelAgent(
            agent_id="sentinel-0",
            session=session,
            config=self.rt.config.sentinel,
            client=client,
            runtime=self.rt,
            orchestrator=self,
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
            if self.rt.get_agent(agent_id) is not None:
                return agent_id
            dbmod.set_agent_status(self.rt.db, agent_id, "dead")
        startup_model = self.rt.config.collaborator.startup_model
        harness = get_harness(
            self.rt.config.collaborator.harness, startup_model=startup_model
        )
        session = _session_name(self.rt, "collaborator", "")
        agent = CollaboratorAgent(
            agent_id="collaborator-0",
            session=session,
            harness=harness,
            repo_root=self.rt.repo_root,
            startup_model=startup_model,
            runtime=self.rt,
        )
        self.rt.register_agent(agent)
        body = load(
            (self.rt.config.collaborator.startup_prompt_template or "collaborator.md").removesuffix(
                ".md"
            )
        )
        try:
            await agent.start(body, {})
        except Exception as e:
            reason = f"Collaborator startup failed: {e}"
            dbmod.insert_escalation(
                self.rt.db,
                ticket_id=None,
                severity=2,
                reason=reason,
                to_recipient="user",
            )
            await self._emit_escalation(None, reason, severity=2)
            await self.rt.reap(agent.id)
            raise
        except BaseException:
            await self.rt.reap(agent.id)
            raise
        return agent.id

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
            await self.rt.reap(f"monkey-{tid}")
            await self.rt.reap(f"augur-{tid}")
        return list(cascaded)

    async def on_writeset_violation(self, ticket_id: str, path: str) -> None:
        if self.rt.bus is None or self.rt.run_id is None or self.rt.db is None:
            return
        dbmod.update_ticket_status(self.rt.db, ticket_id, TicketStatus.BLOCKED.value)
        dbmod.insert_escalation(
            self.rt.db,
            ticket_id=ticket_id,
            severity=2,
            reason=f"Write outside write_set: {path}",
            to_recipient="user",
        )
        await self.rt.bus.publish(
            EscalationEvent(
                run_id=self.rt.run_id,
                agent_id="orchestrator",
                role=AgentRole.SENTINEL,
                ticket_id=ticket_id,
                to="user",
                reason=f"Write-set violation: {path}",
                severity=2,
            )
        )

    async def on_monkey_done(self, ticket_id: str) -> bool:
        assert self.rt.db is not None
        monkey = self.rt.get_monkey(ticket_id)
        start_commit = getattr(monkey, "start_commit", None) if monkey else None
        if not start_commit:
            await self._fail_ticket(
                ticket_id,
                "Monkey reported done without a recorded start commit; no diff validation ran.",
            )
            return False
        row = dbmod.get_ticket(self.rt.db, ticket_id)
        if row is None:
            return False
        write_paths = [Path(p) for p in row.get("write_set") or []]
        missing = _missing_write_set_paths(self.rt.repo_root, write_paths)
        if missing:
            await self._fail_ticket(
                ticket_id,
                "write_set artefacts missing: " + ", ".join(missing[:8]),
            )
            return False
        checklist = verify_checklist(self.rt.db, ticket_id, self.rt.repo_root)
        if not checklist.overall_ok:
            await self._fail_ticket(ticket_id, format_report(checklist))
            return False
        dirty = await git_diff.diff_outside(self.rt.repo_root, start_commit, write_paths)
        if dirty:
            await self._fail_ticket(ticket_id, f"Diff outside write_set: {dirty[:5]}")
            return False
        prev = lifecycle.transition(self.rt.db, ticket_id, TicketStatus.DONE)
        await self._emit_ticket_status(ticket_id, prev, TicketStatus.DONE.value)
        return True
