"""NotetakerAgent — planning-mode scribe backed by an OpenRouter LLM.

D1-style native coroutine (no tmux): it holds an API message list, tidies the
user's stream-of-consciousness into the dated notes document via `read_notes`
/ `write_notes` tools, and replies in chat with follow-up questions and
suggestions. `session` names a virtual session for debug parity with the
other native agents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder import notes
from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.clients.base import ToolSpec
from murder.config import NotetakerConfig
from murder.prompts import load

if TYPE_CHECKING:
    from murder.clients.base import APIClient
    from murder.runtime import Runtime

_MAX_TOOL_ITERS = 8
_SEED_CALL_ID = "seed-read-notes"
_NO_CLIENT_REPLY = (
    "Notetaker is offline — no OpenRouter client configured. "
    "Set OPENROUTER_API_KEY (see `murder doctor`) and pick a model in Settings."
)


class NotetakerAgent(Agent):
    role = AgentRole.NOTETAKER
    ticket_id = None

    def __init__(
        self,
        agent_id: str,
        session: str,
        config: NotetakerConfig,
        client: "APIClient | None",
        *,
        repo_root: Path,
        runtime: "Runtime",
        note_name: str,
    ) -> None:
        self.id = agent_id
        self.session = session
        self.config = config
        self.client = client
        self.repo_root = Path(repo_root)
        self.runtime = runtime
        self.note_name = note_name
        self.status = AgentStatus.IDLE
        self._system = load("notetaker")
        self.messages: list[dict[str, Any]] = []
        self._seed_len = 0

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        del brief, ctx
        from murder.bus import StatusChangeEvent

        db = self.runtime.db
        if db is not None:
            notes.ensure_note(db, self.repo_root, self.note_name)
            body = notes.read_note(db, self.note_name)
            prior = notes.latest_prior_note(db, exclude=self.note_name) if not body else None
        else:
            body, prior = "", None

        seed_result = body or "(today's notes document is empty)"
        if prior is not None:
            prior_name, prior_body = prior
            seed_result += f"\n\n— previous session ({prior_name}) —\n{prior_body}"
        self.messages = [
            {
                "role": "assistant",
                "content": f"Let me check the current notes document ({self.note_name}).",
                "tool_calls": [
                    {
                        "id": _SEED_CALL_ID,
                        "type": "function",
                        "function": {"name": "read_notes", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": _SEED_CALL_ID, "content": seed_result},
        ]
        self._seed_len = len(self.messages)

        self.status = AgentStatus.RUNNING
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)
        if self.runtime.bus is not None and self.runtime.run_id is not None:
            await self.runtime.bus.publish(
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

    async def stop(self, *, failed: bool = False) -> None:
        self.status = AgentStatus.FAILED if failed else AgentStatus.DONE
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        await self.reply_to(msg)

    # ── conversation ───────────────────────────────────────────────────────

    async def reply_to(self, user_text: str) -> str:
        """Run one user turn through the tool loop; return the chat reply."""
        self.messages.append({"role": "user", "content": user_text})
        if self.client is None:
            self.messages.append({"role": "assistant", "content": _NO_CLIENT_REPLY})
            return _NO_CLIENT_REPLY
        try:
            return await self._run_tool_loop()
        except Exception as e:  # noqa: BLE001 — surface to the chat pane, don't crash the TUI
            reply = f"(notetaker error: {e})"
            self.messages.append({"role": "assistant", "content": reply})
            return reply

    async def _run_tool_loop(self) -> str:
        assert self.client is not None
        text = ""
        for _ in range(_MAX_TOOL_ITERS):
            r = await self.client.complete(
                model=self.config.model,
                system=self._system,
                messages=self.messages,
                tools=self._tool_specs(),
                max_tokens=self.config.max_tokens,
            )
            text = r.text or ""
            if not r.tool_calls:
                self.messages.append({"role": "assistant", "content": text})
                return text
            self.messages.append(
                {
                    "role": "assistant",
                    "content": text,
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
                result = self._dispatch_tool(tc.name, tc.arguments)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.call_id,
                        "content": json.dumps(result),
                    }
                )
        # Ran out of tool iterations without a final answer.
        reply = text or "(notetaker hit its tool-call limit without replying)"
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    # ── UI projection ──────────────────────────────────────────────────────

    def transcript_for_ui(self) -> list[tuple[str, str]]:
        """User/notetaker chat turns, derived from `messages` (single source of truth)."""
        out: list[tuple[str, str]] = [
            ("notetaker", f"📄 Read current notes ({self.note_name}).")
        ]
        for m in self.messages[self._seed_len :]:
            role = m.get("role")
            content = m.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if role == "user":
                out.append(("you", content))
            elif role == "assistant" and "tool_calls" not in m:
                out.append(("notetaker", content))
        return out

    # ── tools ──────────────────────────────────────────────────────────────

    def _tool_specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="read_notes",
                description="Return the current contents of the notes document.",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="write_notes",
                description=(
                    "Replace the entire notes document with `content`. "
                    "There is no append — always send the full updated document."
                ),
                parameters={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            ),
        ]

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> Any:
        db = self.runtime.db
        if db is None:
            return {"error": "no database"}
        try:
            if name == "read_notes":
                body = notes.read_note(db, self.note_name)
                return body or "(notes document is empty)"
            if name == "write_notes":
                content = str(args.get("content", ""))
                notes.write_note(db, self.repo_root, self.note_name, content)
                return {"ok": True, "chars": len(content)}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        return {"error": f"unknown tool {name}"}
