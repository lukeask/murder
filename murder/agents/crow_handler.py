"""CrowHandlerAgent — per-Crow driver (D1: native coroutine, not a tmux pane)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from murder.agents.base import Agent, AgentRole, AgentStatus
from murder.config import CrowHandlerConfig
from murder.harnesses.base import HarnessAdapter

if TYPE_CHECKING:
    from murder.clients.base import APIClient
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime


class CrowHandlerAgent(Agent):
    role = AgentRole.CROW_HANDLER

    def __init__(
        self,
        agent_id: str,
        ticket_id: str,
        session: str,
        crow_session: str,
        harness: HarnessAdapter,
        config: CrowHandlerConfig,
        *,
        repo_root: Path,
        runtime: "Runtime",
        orchestrator: "Orchestrator",
        client: "APIClient | None" = None,
    ) -> None:
        self.id = agent_id
        self.ticket_id = ticket_id
        self.session = session
        self.crow_session = crow_session
        self.harness = harness
        self.config = config
        self.repo_root = Path(repo_root)
        self.runtime = runtime
        self._orch = orchestrator
        self._client = client
        self.status = AgentStatus.IDLE
        self._tick_count = 0
        self._stuck_ticks = 0
        self._last_pane_hash: str | None = None
        self._last_summary: str | None = None
        self._idle_cached = False
        self._on_idle_callbacks: list[asyncio.Future[None]] = []
        self._poll_task: asyncio.Task[None] | None = None
        self._done_emitted = False
        self._log_path: Path | None = None
        self._consecutive_tick_failures = 0
        self._max_tick_failures = 3

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder import tmux
        from murder.bus import StatusChangeEvent
        from murder.storage.runs import open_pane_log

        assert self.runtime.run_id is not None
        self._log_path = open_pane_log(
            self.repo_root, self.runtime.run_id, f"crow_handler_{self.ticket_id}"
        )
        self._log_path.write_text(
            f"# crow_handler log for {self.ticket_id}\n", encoding="utf-8"
        )
        await tmux.create_session(
            self.session,
            self.repo_root,
            ["tail", "-f", str(self._log_path)],
        )
        self.status = AgentStatus.RUNNING
        self.runtime.sync_agent(self)
        if self.runtime.bus and self.runtime.run_id:
            await self.runtime.bus.publish(
                StatusChangeEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    entity="agent",
                    entity_id=self.id,
                    from_status=AgentStatus.IDLE.value,
                    to_status=AgentStatus.RUNNING.value,
                )
            )

        async def _loop() -> None:
            while self.status == AgentStatus.RUNNING:
                try:
                    await self.tick()
                    self._consecutive_tick_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    await self._record_tick_failure(e)
                    if self._consecutive_tick_failures >= self._max_tick_failures:
                        break
                await asyncio.sleep(self.config.poll_interval_s)

        self._poll_task = asyncio.create_task(_loop())

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del kill_session  # crow_handler has no real tmux session
        from murder import tmux

        if failed or self.status == AgentStatus.FAILED:
            self.status = AgentStatus.FAILED
        elif self.status != AgentStatus.DEAD:
            self.status = AgentStatus.DONE
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poll_task
            self._poll_task = None
        self._fail_idle_waiters(RuntimeError("crow_handler stopped before crow became idle"))
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)

    async def send(self, msg: str) -> None:
        return None

    def _fire_idle_callbacks_if_idle(self) -> None:
        if not self._idle_cached:
            return
        for fut in self._on_idle_callbacks:
            if not fut.done():
                fut.set_result(None)
        self._on_idle_callbacks.clear()

    async def tick(self) -> None:
        from murder import db as dbmod
        from murder import tmux
        from murder.bus import (
            HeartbeatEvent,
            QuestionEvent,
            SummaryEvent,
        )
        from murder.storage.paths import ticket_md
        from murder.tickets import parser as ticket_parser

        if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
            return

        pane = await tmux.capture_pane(
            self.crow_session, lines=self.config.context_lines
        )
        self._idle_cached = self.harness.is_idle(pane)
        self._fire_idle_callbacks_if_idle()

        h = hashlib.sha256(pane.encode("utf-8", errors="replace")).hexdigest()
        pane_unchanged = h == self._last_pane_hash
        self._last_pane_hash = h

        for ask in self.harness.detect_asks(pane):
            await self.runtime.bus.publish(
                QuestionEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    question=ask,
                    crow_session=self.crow_session,
                    recent_pane=pane[-4000:],
                )
            )

        for check in self.harness.detect_checks(pane):
            dbmod.check_off_item(self.runtime.db, self.ticket_id, check)

        tpath = ticket_md(self.repo_root, self.ticket_id)
        for note in self.harness.detect_notes(pane):
            ticket_parser.append_section(tpath, "Working notes", f">>> NOTE: {note}")

        if self.harness.detect_done(pane) and not self._done_emitted:
            self._done_emitted = True
            await self._orch.on_crow_done(self.ticket_id)
            return

        excerpt = self.harness.extract_last_message(pane) or ""
        done_n, total = dbmod.checklist_progress(self.runtime.db, self.ticket_id)

        if pane_unchanged and self._idle_cached:
            self._stuck_ticks += 1
        else:
            self._stuck_ticks = 0

        if self._stuck_ticks >= self.config.stuck_threshold_ticks:
            state, summary = await self._classify(pane)
            self._last_summary = summary
            hb_state = _heartbeat_state(state)
            await self.runtime.bus.publish(
                HeartbeatEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    state=hb_state,
                    summary=summary,
                )
            )
            self._stuck_ticks = 0

        self._tick_count += 1
        if (
            self._tick_count > 0
            and self._tick_count % max(1, self.config.forced_summary_every_n_ticks) == 0
        ):
            await self.runtime.bus.publish(
                SummaryEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    text=self._last_summary or excerpt[:200] or "(crow_handler)",
                    checklist_done=done_n,
                    checklist_total=total,
                    last_message_excerpt=excerpt[:500],
                )
            )
        dbmod.heartbeat_agent(self.runtime.db, self.id)

    def is_crow_idle(self) -> bool:
        return self._idle_cached

    async def await_idle(self) -> None:
        if self._idle_cached:
            return
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._on_idle_callbacks.append(fut)
        try:
            await fut
        finally:
            if fut in self._on_idle_callbacks:
                self._on_idle_callbacks.remove(fut)

    async def _record_tick_failure(self, exc: Exception) -> None:
        from murder.bus import ErrorEvent, StatusChangeEvent

        self._consecutive_tick_failures += 1
        terminal = self._consecutive_tick_failures >= self._max_tick_failures
        if terminal:
            prev = self.status
            self.status = AgentStatus.DEAD
            self.runtime.sync_agent(self)
            self._fail_idle_waiters(
                RuntimeError("crow_handler stopped after repeated poll failures")
            )
            if self.runtime.bus and self.runtime.run_id:
                await self.runtime.bus.publish(
                    StatusChangeEvent(
                        run_id=self.runtime.run_id,
                        agent_id=self.id,
                        role=self.role,
                        ticket_id=self.ticket_id,
                        entity="agent",
                        entity_id=self.id,
                        from_status=prev.value,
                        to_status=AgentStatus.DEAD.value,
                        reason=str(exc),
                    )
                )
        if self.runtime.bus and self.runtime.run_id:
            await self.runtime.bus.publish(
                ErrorEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    message=f"crow_handler tick failed: {exc}",
                    recoverable=not terminal,
                )
            )

    def _fail_idle_waiters(self, exc: Exception) -> None:
        for fut in self._on_idle_callbacks:
            if not fut.done():
                fut.set_exception(exc)
        self._on_idle_callbacks.clear()

    async def _classify(self, pane: str) -> tuple[str, str | None]:
        if self._client is None:
            return "progressing", None
        sys_p = (
            "Classify the coding agent pane. Reply with a single JSON object only, "
            'keys: "state" (one of progressing, stuck, thinking) and "summary" '
            "(short string or empty)."
        )
        try:
            r = await self._client.complete(
                model=self.config.model,
                system=sys_p,
                messages=[{"role": "user", "content": pane[-12_000:]}],
                max_tokens=120,
                temperature=0.0,
            )
        except Exception:
            return "thinking", None
        if not r.text:
            return "progressing", None
        try:
            data = json.loads(r.text.strip())
            st = str(data.get("state", "progressing"))
            if st not in ("progressing", "stuck", "thinking"):
                st = "progressing"
            return st, data.get("summary")  # type: ignore[return-value]
        except json.JSONDecodeError:
            return "progressing", r.text[:200]


def _heartbeat_state(s: str) -> Literal["progressing", "stuck", "thinking"]:
    if s == "stuck":
        return cast(Literal["stuck"], "stuck")
    if s == "thinking":
        return cast(Literal["thinking"], "thinking")
    return cast(Literal["progressing"], "progressing")
