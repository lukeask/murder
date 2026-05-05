"""SentinelAgent — global tech-lead overseer (one per project)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder import db as dbmod
from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.bus import EscalationEvent, QuestionEvent, TicketStatus
from murder.clients.base import ToolSpec
from murder.config import SentinelConfig
from murder.storage.filesystem import atomic_write_text
from murder.storage.paths import escalations_dir, ticket_md
from murder.tickets import lifecycle, parser as ticket_parser

MONKEY_IDLE_WAIT_TIMEOUT_S = 60.0

if TYPE_CHECKING:
    from murder.clients.base import APIClient
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime


class SentinelAgent(Agent):
    role = AgentRole.SENTINEL
    ticket_id = None

    def __init__(
        self,
        agent_id: str,
        session: str,
        config: SentinelConfig,
        client: "APIClient | None",
        *,
        runtime: "Runtime",
        orchestrator: "Orchestrator",
    ) -> None:
        self.id = agent_id
        self.session = session
        self.config = config
        self.client = client
        self.runtime = runtime
        self._orch = orchestrator
        self.status = AgentStatus.IDLE
        self._sub_handle: Any = None

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.bus import StatusChangeEvent

        bus = self.runtime.bus
        if bus is not None and self.runtime.run_id is not None:

            async def _route(event: Any) -> None:
                if getattr(event, "type", None) == "question":
                    await self.on_question(event)

            self._sub_handle = bus.subscribe(_route, None)
            await bus.publish(
                StatusChangeEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=None,
                    entity="agent",
                    entity_id=self.id,
                    from_status=AgentStatus.IDLE.value,
                    to_status=AgentStatus.RUNNING.value,
                )
            )
        self.status = AgentStatus.RUNNING
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def stop(self, *, failed: bool = False) -> None:
        if self._sub_handle is not None:
            self._sub_handle.cancel()
            self._sub_handle = None
        self.status = AgentStatus.FAILED if failed else AgentStatus.DONE
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        """Reserved for TUI-forwarded prompts (M5)."""
        del msg

    async def on_augur_escalation(self, event: EscalationEvent) -> None:
        """Escalations are already persisted via the bus; hook reserved for policy."""
        del event

    async def on_question(self, event: QuestionEvent) -> None:
        if self.runtime.bus is None or self.runtime.run_id is None:
            return
        if self.client is None:
            await self.tool_escalate_user(
                f"(no API client) question on {event.ticket_id}: {event.question[:500]}",
                2,
            )
            return
        from murder.prompts import load

        tools = self._tool_specs()
        system = load("sentinel")
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "ticket_id": event.ticket_id,
                        "question": event.question,
                        "monkey_session": event.monkey_session,
                    }
                ),
            }
        ]
        for _ in range(6):
            r = await self.client.complete(
                model=self.config.model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=1024,
            )
            if not r.tool_calls:
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": r.text,
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in r.tool_calls
                    ],
                }
            )
            for tc in r.tool_calls:
                result = await self._dispatch_tool(tc.name, tc.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.call_id,
                        "content": json.dumps(result),
                    }
                )

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> Any:
        try:
            if name == "read_file":
                return await self.tool_read_file(
                    str(args.get("path", "")), int(args.get("max_lines", 500))
                )
            if name == "grep":
                return await self.tool_grep(
                    str(args.get("pattern", "")), str(args.get("glob", "**/*"))
                )
            if name == "list_tickets":
                return await self.tool_list_tickets(
                    int(args["wave"]) if args.get("wave") is not None else None
                )
            if name == "read_ticket":
                return await self.tool_read_ticket(str(args.get("ticket_id", "")))
            if name == "send_to_monkey":
                return await self.tool_send_to_monkey(
                    str(args.get("ticket_id", "")), str(args.get("msg", ""))
                )
            if name == "escalate_user":
                await self.tool_escalate_user(
                    str(args.get("reason", "")), int(args.get("severity", 2))
                )
                return {"ok": True}
            if name == "escalate_collaborator":
                await self.tool_escalate_collaborator(
                    str(args.get("reason", "")), str(args.get("body", ""))
                )
                return {"ok": True}
            if name == "append_sentinel_note":
                await self.tool_append_sentinel_note(
                    str(args.get("ticket_id", "")), str(args.get("note", ""))
                )
                return {"ok": True}
            if name == "pause_ticket":
                await self.tool_pause_ticket(
                    str(args.get("ticket_id", "")), str(args.get("reason", ""))
                )
                return {"ok": True}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"unknown tool {name}"}

    def _tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="read_file",
                description="Read a text file under the repo (relative path).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_lines": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="grep",
                description="Search files matching glob for substring pattern.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "glob": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            ),
            ToolSpec(
                name="list_tickets",
                description="List tickets; optional wave filter.",
                parameters={
                    "type": "object",
                    "properties": {"wave": {"type": "integer"}},
                },
            ),
            ToolSpec(
                name="read_ticket",
                description="Load ticket aggregate from DB.",
                parameters={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}},
                    "required": ["ticket_id"],
                },
            ),
            ToolSpec(
                name="send_to_monkey",
                description="Send a nudge to the monkey after idle gate.",
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "string"},
                        "msg": {"type": "string"},
                    },
                    "required": ["ticket_id", "msg"],
                },
            ),
            ToolSpec(
                name="escalate_user",
                description="Escalate to the human user.",
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "severity": {"type": "integer"},
                    },
                    "required": ["reason"],
                },
            ),
            ToolSpec(
                name="escalate_collaborator",
                description="Route to collaborator with markdown body.",
                parameters={
                    "type": "object",
                    "properties": {"reason": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["reason", "body"],
                },
            ),
            ToolSpec(
                name="append_sentinel_note",
                description="Append to Sentinel notes on a ticket.",
                parameters={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}, "note": {"type": "string"}},
                    "required": ["ticket_id", "note"],
                },
            ),
            ToolSpec(
                name="pause_ticket",
                description="Pause a ticket (blocked) and interrupt monkey.",
                parameters={
                    "type": "object",
                    "properties": {"ticket_id": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["ticket_id", "reason"],
                },
            ),
        ]

    async def tool_read_file(self, path: str, max_lines: int = 500) -> str:
        repo = self.runtime.repo_root.resolve()
        p = (self.runtime.repo_root / path).resolve()
        try:
            p.relative_to(repo)
        except ValueError:
            return "error: path escapes repo"
        if not p.is_file():
            return "error: not a file"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:max_lines])

    async def tool_grep(self, pattern: str, glob: str = "**/*") -> list[str]:
        out: list[str] = []
        root = self.runtime.repo_root
        for f in root.glob(glob):
            if len(out) >= 200:
                break
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern in text:
                out.append(str(f.relative_to(root)))
        return out[:200]

    async def tool_list_tickets(self, wave: int | None = None) -> list[dict[str, Any]]:
        if self.runtime.db is None:
            return []
        if wave is None:
            rows = self.runtime.db.execute(
                "SELECT id, title, wave, status FROM tickets ORDER BY wave, id"
            ).fetchall()
        else:
            rows = self.runtime.db.execute(
                "SELECT id, title, wave, status FROM tickets WHERE wave = ? ORDER BY id",
                (wave,),
            ).fetchall()
        return [dict(r) for r in rows]

    async def tool_read_ticket(self, ticket_id: str) -> dict[str, Any]:
        if self.runtime.db is None:
            return {}
        row = dbmod.get_ticket(self.runtime.db, ticket_id)
        return row or {}

    async def tool_send_to_monkey(self, ticket_id: str, msg: str) -> dict[str, Any]:
        aug = self.runtime.get_augur(ticket_id)
        if aug is None:
            return {"error": "no augur"}
        monkey = self.runtime.get_monkey(ticket_id)
        if monkey is None:
            return {"error": "no monkey"}
        if not aug.is_monkey_idle():
            try:
                await asyncio.wait_for(
                    aug.await_idle(),
                    timeout=MONKEY_IDLE_WAIT_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return {"error": "monkey did not become idle in time"}
            except Exception as e:
                return {"error": f"monkey idle wait failed: {e}"}
        await monkey.send(msg)
        return {"ok": True}

    async def tool_escalate_user(self, reason: str, severity: int) -> None:
        if self.runtime.bus is None or self.runtime.run_id is None or self.runtime.db is None:
            return
        sev = severity if severity in (1, 2, 3) else 2
        dbmod.insert_escalation(
            self.runtime.db,
            ticket_id=None,
            severity=sev,
            reason=reason,
            to_recipient="user",
        )
        await self.runtime.bus.publish(
            EscalationEvent(
                run_id=self.runtime.run_id,
                agent_id=self.id,
                role=self.role,
                ticket_id=None,
                to="user",
                reason=reason,
                severity=sev,  # type: ignore[arg-type]
            )
        )

    async def tool_escalate_collaborator(self, reason: str, body: str) -> None:
        if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
            return
        eid = dbmod.insert_escalation(
            self.runtime.db,
            ticket_id=None,
            severity=2,
            reason=reason,
            to_recipient="collaborator",
            body_path=None,
        )
        path = escalations_dir(self.runtime.repo_root) / f"{eid}.md"
        atomic_write_text(path, body)
        self.runtime.db.execute(
            "UPDATE escalations SET body_path = ? WHERE id = ?",
            (str(path), eid),
        )
        await self.runtime.bus.publish(
            EscalationEvent(
                run_id=self.runtime.run_id,
                agent_id=self.id,
                role=self.role,
                ticket_id=None,
                to="collaborator",
                reason=reason,
                severity=2,
            )
        )

    async def tool_append_sentinel_note(self, ticket_id: str, note: str) -> None:
        path = ticket_md(self.runtime.repo_root, ticket_id)
        ticket_parser.append_section(path, "Sentinel notes", note)

    async def tool_pause_ticket(self, ticket_id: str, reason: str) -> None:
        del reason
        if self.runtime.db is None:
            return
        with contextlib.suppress(lifecycle.InvalidTransition):
            lifecycle.transition(self.runtime.db, ticket_id, TicketStatus.BLOCKED)
        monkey = self.runtime.get_monkey(ticket_id)
        if monkey is not None:
            await monkey.harness_session.interrupt()
