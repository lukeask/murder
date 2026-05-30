"""CompletionCoordinator — runs checks and dispatches resolution actions."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from murder.bus import Bus
from murder.bus import Role as AgentRole

from .checks.base import CheckResult, CheckStatus, CompletionContext
from .persistence import bump_attempts, get_attempts, reset_attempts, write_check_result
from .policy import Owner, resolution_policy
from .registry import CheckRegistry

if TYPE_CHECKING:
    from murder.agents.base import Agent

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DoneHandleResult:
    completed: bool
    failed_checks: tuple[str, ...] = ()


class CoordinatorHost(Protocol):
    """Subset of runtime the coordinator requires."""

    repo_root: Path
    db: sqlite3.Connection | None
    bus: Bus | None
    run_id: str | None

    def get_crow(self, ticket_id: str) -> Agent | None: ...

    def get_agent(self, agent_id: str) -> Agent | None: ...


EnsurePlanner = Callable[[str], Awaitable[str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _get_plan_name(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    has_plan_tickets = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plan_tickets'"
    ).fetchone()
    table = "plan_tickets" if has_plan_tickets is not None else "plan_related_tickets"
    row = conn.execute(
        f"SELECT plan_name FROM {table} WHERE ticket_id = ? LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return str(row["plan_name"]) if row else None


class CompletionCoordinator:
    def __init__(
        self,
        rt: CoordinatorHost,
        registry: CheckRegistry,
        *,
        ensure_planning_agent: EnsurePlanner | None = None,
    ) -> None:
        self._rt = rt
        self._registry = registry
        self._ensure_planning_agent = ensure_planning_agent

    async def handle_done(
        self,
        ticket_id: str,
        *,
        crow_session: str,
        start_commit: str | None,
        repo_root: Path | None = None,
    ) -> DoneHandleResult:
        from murder.persistence.tickets import get_ticket as _db_get_ticket

        if self._rt.db is None:
            return DoneHandleResult(completed=False)

        conn = self._rt.db
        row = _db_get_ticket(conn, ticket_id)
        if row is None:
            LOGGER.warning("coordinator.handle_done: ticket %s not found", ticket_id)
            return DoneHandleResult(completed=False)

        write_set = tuple(Path(p) for p in (row.get("write_set") or []))
        ctx = CompletionContext(
            ticket_id=ticket_id,
            write_set=write_set,
            repo_root=repo_root or self._rt.repo_root,
            db=conn,
            start_commit=start_commit,
        )

        checks = self._registry.assigned_checks(row)
        timestamp = _now()

        results: list[tuple[str, CheckResult | None]] = []
        for check in checks:
            try:
                result = await check.run(ctx)
            except Exception as exc:
                LOGGER.error("check %s raised: %s", check.name, exc)
                write_check_result(conn, ticket_id, check.name, timestamp, "fail", None)
                results.append((check.name, None))
                continue

            data_json = json.dumps({"message": result.message, "hint": result.hint})
            write_check_result(conn, ticket_id, check.name, timestamp, result.status.value, data_json)
            results.append((check.name, result))

        passes = [(name, r) for name, r in results if r is not None and r.status == CheckStatus.PASS]
        failures = [(name, r) for name, r in results if r is None or r.status == CheckStatus.FAIL]

        for name, _ in passes:
            reset_attempts(conn, ticket_id, name)

        if not failures:
            await self._transition_done(ticket_id)
            return DoneHandleResult(completed=True)

        reprompt_msgs: list[str] = []
        for name, result in failures:
            n = get_attempts(conn, ticket_id, name)
            bump_attempts(conn, ticket_id, name)
            owner = resolution_policy(name, n)
            await self._dispatch(
                owner,
                ticket_id=ticket_id,
                check_name=name,
                result=result,
                crow_session=crow_session,
                reprompt_msgs=reprompt_msgs,
            )

        if reprompt_msgs:
            crow = self._rt.get_crow(ticket_id)
            if crow is not None:
                combined = "The following checks failed. Please fix them:\n\n" + "\n\n".join(reprompt_msgs)
                await crow.send(combined)

        return DoneHandleResult(
            completed=False,
            failed_checks=tuple(name for name, _ in failures),
        )

    async def _dispatch(
        self,
        owner: Owner,
        *,
        ticket_id: str,
        check_name: str,
        result: CheckResult | None,
        crow_session: str,
        reprompt_msgs: list[str],
    ) -> None:
        if owner == Owner.REPROMPT:
            if result is not None:
                msg = result.hint or result.message
            else:
                msg = f"Check '{check_name}' failed with an internal error."
            reprompt_msgs.append(f"[{check_name}] {msg}")

        elif owner == Owner.ASK_PLANNER:
            await self._ask_planner(ticket_id, check_name, result, crow_session)

        elif owner == Owner.ASK_USER:
            if self._rt.db is None:
                return
            reason = self._format_failure_reason(check_name, result)
            await self._escalate_to_user(ticket_id, reason)
            await self._block_ticket(ticket_id)

        elif owner == Owner.FAIL_TICKET:
            reason = self._format_failure_reason(check_name, result)
            await self._fail_ticket(ticket_id, reason)

    async def _ask_planner(
        self,
        ticket_id: str,
        check_name: str,
        result: CheckResult | None,
        crow_session: str,
    ) -> None:
        if self._rt.db is None:
            return

        plan_name = _get_plan_name(self._rt.db, ticket_id)
        if plan_name is None:
            reason = self._format_failure_reason(check_name, result)
            await self._escalate_to_user(ticket_id, f"[no plan] {reason}")
            return

        if self._ensure_planning_agent is not None:
            try:
                await self._ensure_planning_agent(plan_name)
            except Exception as exc:
                LOGGER.warning("ensure_planning_agent failed for %s: %s", plan_name, exc)

        from murder.agents.planning_handler import PlanningHandler

        handler = self._rt.get_agent(f"planning_handler-{plan_name}")
        if not isinstance(handler, PlanningHandler):
            reason = self._format_failure_reason(check_name, result)
            LOGGER.warning("no live planner for plan %s — escalating to user", plan_name)
            await self._escalate_to_user(ticket_id, f"[no planner] {reason}")
            return

        question = (
            f"The following check failed for ticket {ticket_id}. "
            f"What should the crow do to resolve it?\n\n"
            f"Check: {check_name}\n"
            f"{self._format_failure_reason(check_name, result)}"
        )
        await handler.relay_ask(ticket_id, question, crow_session)

    def _format_failure_reason(self, check_name: str, result: CheckResult | None) -> str:
        if result is None:
            return f"Check '{check_name}' failed with an internal error."
        if result.hint:
            return f"{result.message}: {result.hint}"
        return result.message

    async def _transition_done(self, ticket_id: str) -> None:
        from murder.persistence.tickets import get_ticket_status
        from murder.tickets import lifecycle
        from murder.tickets.status import TicketStatus
        from murder.bus import StatusChangeEvent

        if self._rt.db is None:
            return
        status = get_ticket_status(self._rt.db, ticket_id)
        if status == TicketStatus.READY.value:
            lifecycle.transition(
                self._rt.db,
                ticket_id,
                TicketStatus.IN_PROGRESS,
                reason="completion",
            )
        prev = lifecycle.transition(self._rt.db, ticket_id, TicketStatus.DONE)
        if self._rt.bus is not None and self._rt.run_id is not None:
            await self._rt.bus.publish(
                StatusChangeEvent(
                    run_id=self._rt.run_id,
                    agent_id="coordinator",
                    role=AgentRole.COLLABORATOR,
                    ticket_id=ticket_id,
                    entity="ticket",
                    entity_id=ticket_id,
                    from_status=prev.value if hasattr(prev, "value") else str(prev),
                    to_status=TicketStatus.DONE.value,
                )
            )
        await self._prune_terminal_worktree(ticket_id)

    async def _fail_ticket(self, ticket_id: str, reason: str) -> None:
        from murder.tickets import lifecycle
        from murder.tickets.status import TicketStatus
        from murder.bus import StatusChangeEvent
        from murder.persistence.tickets import update_ticket_status

        if self._rt.db is None:
            return

        try:
            prev = lifecycle.transition(self._rt.db, ticket_id, TicketStatus.FAILED)
        except Exception:
            update_ticket_status(self._rt.db, ticket_id, TicketStatus.FAILED.value)
            prev = TicketStatus.IN_PROGRESS

        lifecycle.set_last_error(self._rt.db, ticket_id, reason)

        if self._rt.bus is not None and self._rt.run_id is not None:
            await self._rt.bus.publish(
                StatusChangeEvent(
                    run_id=self._rt.run_id,
                    agent_id="coordinator",
                    role=AgentRole.COLLABORATOR,
                    ticket_id=ticket_id,
                    entity="ticket",
                    entity_id=ticket_id,
                    from_status=prev.value if hasattr(prev, "value") else str(prev),
                    to_status=TicketStatus.FAILED.value,
                )
            )
        await self._make_escalation_service().record_ticket_failure(ticket_id, reason)
        await self._prune_terminal_worktree(ticket_id)

    async def _escalate_to_user(self, ticket_id: str, reason: str) -> None:
        esc = self._make_escalation_service()
        await esc.escalate_to_user(reason, severity=2, ticket_id=ticket_id)

    async def _block_ticket(self, ticket_id: str) -> None:
        if self._rt.db is None:
            return
        from murder.tickets.status import TicketStatus
        from murder.persistence.tickets import update_ticket_status
        update_ticket_status(self._rt.db, ticket_id, TicketStatus.BLOCKED.value)
        self._rt.db.commit()

    def _make_escalation_service(self) -> object:
        from murder.escalations.service import EscalationService

        assert self._rt.db is not None
        return EscalationService(
            conn=self._rt.db,
            repo_root=self._rt.repo_root,
            bus=self._rt.bus,
            run_id=self._rt.run_id,
            agent_id="coordinator",
            role=AgentRole.COLLABORATOR,
        )

    async def _prune_terminal_worktree(self, ticket_id: str) -> None:
        if self._rt.db is None:
            return
        from murder.storage.worktrees import prune_terminal_crow_worktree

        try:
            await prune_terminal_crow_worktree(self._rt.db, self._rt.repo_root, ticket_id)
        except Exception as exc:
            LOGGER.debug("worktree prune skipped for %s: %s", ticket_id, exc)


__all__ = ["CompletionCoordinator", "CoordinatorHost", "DoneHandleResult"]
