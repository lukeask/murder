"""CrowHandler — per-Crow driver (D1: native coroutine, not a tmux pane)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from murder.observability.log_context import log_context
from murder.runtime.agents.base import Daemon, AgentRole, AgentStatus, TRANSCRIPT_SCROLLBACK_LINES
from murder.verdict.completion import CompletionCoordinator
from murder.config import CrowHandlerConfig
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.results import SimpleResult
from murder.runtime.orchestration.outcome import TicketOutcomeService
from murder.work.tickets.status import TicketStatus

if TYPE_CHECKING:
    from murder.llm.clients.base import APIClient
    from murder.app.service.runtime_scope import AgentLifecycleHost as Runtime


# A single transient tmux/SQLite tick blip should not permanently fail a
# ticket. Only after this many *consecutive* tick failures do we go terminal.
TICK_FAILURE_BUDGET = 3


class CrowHandler(Daemon):
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
        runtime: Runtime,
        outcome: TicketOutcomeService,
        coordinator: CompletionCoordinator,
        workspace_root: Path | None = None,
        client: APIClient | None = None,
    ) -> None:
        self.id = agent_id
        self.ticket_id = ticket_id
        self.session = session
        self.crow_session = crow_session
        self.harness = harness
        self.config = config
        self.repo_root = Path(repo_root)
        self.workspace_root = (
            Path(workspace_root) if workspace_root is not None else self.repo_root
        )
        self.runtime = runtime
        self.outcome = outcome
        self.coordinator = coordinator
        self._client = client
        self.status = AgentStatus.IDLE
        self._tick_count = 0
        self._stuck_ticks = 0
        self._last_pane_hash: str | None = None
        self._last_summary: str | None = None
        self._idle_cached = False
        self._queued_message: str | None = None
        self._on_idle_callbacks: list[asyncio.Future[None]] = []
        self._done_pane_hash: str | None = None
        self._log_path: Path | None = None
        self._terminal_failure = False
        # Set by a tick that detects a terminal ticket; the loop honours it and
        # finalizes after returning, rather than cancelling its own poll task
        # mid-tick via a fire-and-forget create_task(self.stop()).
        self._stop_requested = False
        self._consecutive_tick_failures = 0
        self._last_orchestration_t: float = 0.0
        self._last_orchestration_pane_hash: str | None = None
        # F11 H1: index of the last heartbeat bucket we emitted `agent` for, so a
        # plain beat only invalidates the Ink roster on a bucket crossing (not 5Hz).
        self._last_heartbeat_emit_bucket: int | None = None

    async def start(self, brief: str, ctx: dict[str, Any]) -> None:
        from murder.runtime.terminal import tmux
        from murder.bus import StatusChangeEvent
        from murder.state.storage.run_id_allocation import open_pane_log

        assert self.runtime.run_id is not None
        self._log_path = open_pane_log(
            self.repo_root, self.runtime.run_id, f"crow_handler_{self.ticket_id}"
        )
        self._log_path.write_text(f"# crow_handler log for {self.ticket_id}\n", encoding="utf-8")
        self._log(f"handler started — watching crow session {self.crow_session}")
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

        self._start_loop()

    async def _loop(self) -> None:
        try:
            while (
                self.status == AgentStatus.RUNNING
                and not self._terminal_failure
                and not self._stop_requested
            ):
                try:
                    await self.tick()
                    self._consecutive_tick_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    await self._handle_tick_failure(e)
                if self._stop_requested:
                    break
                interval = (
                    self.config.idle_projection_interval_s
                    if self._idle_cached
                    else self.config.projection_interval_s
                )
                await asyncio.sleep(interval)
        finally:
            if self._terminal_failure:
                await self._finalize_after_tick_failure()
            elif self._stop_requested:
                # We are running *inside* the poll task; clear the handle so
                # super().stop() does not try to cancel-and-await the task we're
                # in (a self-await deadlock). The loop has already exited.
                self._poll_task = None
                await self.stop()

    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None:
        del kill_session  # crow_handler has no real tmux session
        from murder.runtime.terminal import tmux

        if failed or self.status == AgentStatus.FAILED:
            self.status = AgentStatus.FAILED
        elif self.status != AgentStatus.DEAD:
            self.status = AgentStatus.DONE
        await super().stop(failed=failed)
        self._fail_idle_waiters(RuntimeError("crow_handler stopped before crow became idle"))
        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)

    async def send(self, msg: str) -> SimpleResult[None]:
        with log_context(agent_id=self.id):
            return await self.harness.send_prompt(self.crow_session, msg)

    @property
    def pending_message(self) -> str | None:
        return self._queued_message

    async def queue_message(self, msg: str) -> dict[str, bool]:
        """Deliver now if the crow is idle; otherwise hold until the next idle tick."""
        if self.is_crow_idle():
            result = await self.send(msg)
            if not result.ok:
                return {
                    "queued": False,
                    "ok": False,
                    "error": result.message or "crow message delivery failed",
                }
            return {"queued": False}
        self._queued_message = msg
        return {"queued": True}

    async def interrupt_crow(self) -> None:
        await self.harness.interrupt(self.crow_session)

    def _fire_idle_callbacks_if_idle(self) -> None:
        if not self._idle_cached:
            return
        for fut in self._on_idle_callbacks:
            if not fut.done():
                fut.set_result(None)
        self._on_idle_callbacks.clear()

    def _log(self, msg: str) -> None:
        if self._log_path is None:
            return
        import datetime

        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
        with contextlib.suppress(Exception):
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")

    async def tick(self) -> None:
        with log_context(agent_id=self.id):
            await self._tick()

    async def _tick(self) -> None:
        from murder.runtime.terminal import tmux

        if self.runtime.db is None or self.runtime.bus is None or self.runtime.run_id is None:
            return

        pane = await tmux.capture_pane(self.crow_session, lines=TRANSCRIPT_SCROLLBACK_LINES)

        # Transcript projection is owned by the CrowAgent's own loop, not here;
        # this handler only does ticket orchestration off the captured pane.

        # Fast: idle detection + queued message delivery
        was_idle = self._idle_cached
        self._idle_cached = self.harness.is_idle(pane)
        if self._idle_cached and not was_idle and self._queued_message is not None:
            queued = self._queued_message
            self._queued_message = None
            result = await self.send(queued)
            if not result.ok:
                self._log(result.message or "queued message delivery failed")
        self._fire_idle_callbacks_if_idle()

        # Fast: pane hash + done detection (hash-gated to fire once per done state)
        h = hashlib.sha256(pane.encode("utf-8", errors="replace")).hexdigest()
        self._last_pane_hash = h

        if self.harness.detect_done(pane) and h != self._done_pane_hash:
            self._done_pane_hash = h
            await self._run_completion()
            return

        # Slow: orchestration (time-gated; asks/notes are not idempotent at 5Hz)
        now = time.monotonic()
        if now - self._last_orchestration_t >= self.config.poll_interval_s:
            self._last_orchestration_t = now
            await self._orchestration_tick(pane)

    async def _orchestration_tick(self, pane: str) -> None:
        from murder.state.persistence.tickets import get_ticket_status, checklist_progress
        from murder.state.persistence.agents import heartbeat_agent, heartbeat_bucket
        from murder.bus import HeartbeatEvent, NoteEvent, QuestionEvent, SummaryEvent
        from murder.bus.protocol import Entity

        # Stop if ticket reached a terminal state via any path.
        ticket_status = get_ticket_status(self.runtime.db, self.ticket_id)
        if TicketStatus(ticket_status) in (TicketStatus.DONE, TicketStatus.FAILED):
            self._log(f"ticket {self.ticket_id} is {ticket_status} — stopping handler")
            self._stop_requested = True
            return

        # Tail-slice for non-idempotent detectors: keep the same window as the
        # original 40-line capture so markers scroll out between orchestration ticks.
        tail_lines = pane.splitlines()[-self.config.context_lines:]
        tail = "\n".join(tail_lines)

        for ask in self.harness.detect_asks(tail):
            await self.runtime.bus.publish(
                QuestionEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    question=ask,
                    crow_session=self.crow_session,
                    recent_pane=tail,
                )
            )

        # DB-owns-runtime: working notes land in the events table (audit log)
        # via the bus, not the ticket .md. The bus persists every event before
        # fan-out, so the note is durable without clobbering ticket frontmatter
        # or the body checklist.
        for note in self.harness.detect_notes(tail):
            await self.runtime.bus.publish(
                NoteEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    note=note,
                )
            )

        # Stuck detection: compare pane hash between consecutive orchestration ticks.
        h = hashlib.sha256(pane.encode("utf-8", errors="replace")).hexdigest()
        pane_unchanged = h == self._last_orchestration_pane_hash
        self._last_orchestration_pane_hash = h

        excerpt = self.harness.extract_last_message(pane) or ""
        done_n, total = checklist_progress(self.runtime.db, self.ticket_id)

        if pane_unchanged and self._idle_cached:
            self._stuck_ticks += 1
        else:
            self._stuck_ticks = 0

        if self._stuck_ticks >= self.config.stuck_threshold_ticks:
            state, summary = await self._classify(pane)
            self._last_summary = summary
            hb_state = _heartbeat_state(state)
            self._log(f"heartbeat state={hb_state} summary={summary or '—'!r}")
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
        heartbeat_agent(self.runtime.db, self.id)
        # F11 H1: the DB write above always lands, but the key-only `agent`
        # invalidation is coalesced to one emit per HEARTBEAT_EMIT_BUCKET_S so a
        # steady 5Hz heartbeat does not storm the Ink roster refetch. Status
        # changes still invalidate immediately via `sync_agent`.
        bucket = heartbeat_bucket(time.monotonic())
        if bucket != self._last_heartbeat_emit_bucket:
            self._last_heartbeat_emit_bucket = bucket
            await self.runtime.publish_snapshot(Entity.AGENT, self.id)

    async def _run_completion(self) -> None:
        from murder.verdict.completion.coordinator import DoneHandleResult

        self._log(f"crow done detected for {self.ticket_id} — running completion checks…")
        result = await self.coordinator.handle_done(
            self.ticket_id,
            crow_session=self.crow_session,
            repo_root=self.workspace_root,
        )
        if not isinstance(result, DoneHandleResult):
            return

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

    async def _handle_tick_failure(self, exc: Exception) -> None:
        from murder.bus import ErrorEvent, StatusChangeEvent

        if self._terminal_failure:
            return

        error = str(exc)
        self._consecutive_tick_failures += 1
        if self._consecutive_tick_failures < TICK_FAILURE_BUDGET:
            self._log(
                f"tick failure {self._consecutive_tick_failures}/{TICK_FAILURE_BUDGET} "
                f"(transient, will retry): {error}"
            )
            return

        self._terminal_failure = True
        self._log(f"tick failure — failing ticket: {error}")
        await self.outcome.fail_ticket(self.ticket_id, f"crow_handler tick failed: {error}")

        prev = self.status
        self.status = AgentStatus.FAILED
        self.runtime.sync_agent(self)
        self._fail_idle_waiters(RuntimeError(f"crow_handler tick failed: {error}"))
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
                    to_status=AgentStatus.FAILED.value,
                    reason=error,
                )
            )
            await self.runtime.bus.publish(
                ErrorEvent(
                    run_id=self.runtime.run_id,
                    agent_id=self.id,
                    role=self.role,
                    ticket_id=self.ticket_id,
                    message=f"crow_handler tick failed: {error}",
                    recoverable=False,
                )
            )

    async def _finalize_after_tick_failure(self) -> None:
        from murder.runtime.terminal import tmux

        with contextlib.suppress(Exception):
            await tmux.kill_session(self.session)
        if self.runtime.db is not None:
            self.runtime.sync_agent(self)

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
