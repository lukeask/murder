from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from murder.state.persistence.tickets import compute_ready
from murder.state.persistence.commands import enqueue_command
from murder.state.persistence.usage_status import UsageStatusSnapshot, UsageWindow
from murder.bus.protocol import (
    CommandEvent,
    Entity,
    SchedulerDecisionEvent,
    SchedulerModeEvent,
    StateSnapshotEvent,
    UsageResetEvent,
)
from murder.verdict.policy.scheduler_policy import (
    SchedulerCaps,
    SchedulerInput,
    SchedulerParams,
    SchedulerWindow,
    TicketRecord,
    decide,
    usage_reset_detected,
)
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec

_VALID_MODES = frozenset({"manual", "autorun_ready", "crow_magic"})
_VALID_STEERING = frozenset({"auto", "pause", "prefer"})
_TICK_INTERVAL_S = 10.0
_WINDOW_NAME_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)
_WINDOW_NAME_MINUTES = {"h": 60.0, "d": 1440.0}


class SchedulerParamsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    c_changeoff: float = 0.7
    t_alwaysyes: float = 15.0
    alwayscutoff: float = 0.6
    intensity: float = 1.0
    multiharness_cutoff: float | None = None


class SchedulerSetParamsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    harness: str
    window_key: str
    params: SchedulerParamsPayload


def _parse_t_period(window: UsageWindow) -> float | None:
    """Derive t_period (minutes) from a usage window, or return None to skip."""
    end_str = window.ends_at or window.reset_at
    if window.starts_at and end_str:
        try:
            starts_at = datetime.fromisoformat(window.starts_at)
            ends_at = datetime.fromisoformat(end_str)
            period_m = (ends_at - starts_at).total_seconds() / 60.0
            if period_m > 0:
                return period_m
        except (ValueError, TypeError):
            pass
    # Fallback: parse window name like "5h" or "7d"
    m = _WINDOW_NAME_RE.match(window.name)
    if m:
        value = float(m.group(1))
        unit = m.group(2).lower()
        return value * _WINDOW_NAME_MINUTES[unit]
    return None


def _coerce_scheduler_params_row(row: sqlite3.Row) -> SchedulerParamsPayload | None:
    try:
        return SchedulerParamsPayload.model_validate(
            {
                "c_changeoff": row["c_changeoff"],
                "t_alwaysyes": row["t_alwaysyes"],
                "alwayscutoff": row["alwayscutoff"],
                "intensity": row["intensity"],
                "multiharness_cutoff": row["multiharness_cutoff"],
            }
        )
    except ValidationError:
        return None


class SchedulerWorker(Worker):
    """Automates ticket kickoff based on configured scheduler mode.

    Modes:
    - manual: never kicks anything automatically (operator-driven F6 / CLI)
    - autorun_ready: every tick, submits scheduler.kickoff_ready to orchestrator
      when ready tickets with clear deps exist
    - crow_magic: per-(harness, window) usage gate via usage_threshold_curve; per-harness
      priority pick; respects multiharness_cutoff
    """

    SET_MODE = "scheduler.set_mode"
    SET_PARAMS = "scheduler.set_params"
    SET_STEERING = "scheduler.set_steering"

    def __init__(self) -> None:
        super().__init__(
            WorkerSpec(
                name="scheduler",
                accepts=(self.SET_MODE, self.SET_PARAMS, self.SET_STEERING),
                process_model="thread",
            )
        )
        self._tick_seq = 0
        # Track last emitted reset per harness (harness → prev_pct at emit time)
        self._last_reset_prev_pct: dict[str, float] = {}
        self._last_prune_day: str = ""

    async def on_start(self, ctx: WorkerCtx) -> None:
        if ctx.db is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        ctx.db.execute(
            "INSERT OR IGNORE INTO scheduler_state(id, mode, updated_at) VALUES (1, 'manual', ?)",
            (now,),
        )
        self._prune_old_snapshots(ctx.db)
        self._last_prune_day = datetime.now(timezone.utc).date().isoformat()

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_TICK_INTERVAL_S)
                break
            except asyncio.TimeoutError:
                self._tick_seq += 1
                await self._tick(ctx)

    def _prune_old_snapshots(self, db: sqlite3.Connection) -> None:
        db.execute(
            "DELETE FROM harness_usage_snapshots WHERE fetched_at < datetime('now', '-60 days')"
        )

    async def _tick(self, ctx: WorkerCtx) -> None:
        if ctx.db is None or ctx.run_id is None:
            return
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._last_prune_day:
            self._prune_old_snapshots(ctx.db)
            self._last_prune_day = today
        row = ctx.db.execute("SELECT mode FROM scheduler_state WHERE id = 1").fetchone()
        if row is None:
            return
        mode = row["mode"]
        if mode == "autorun_ready":
            await self._tick_autorun_ready(ctx)
        elif mode == "crow_magic":
            await self._tick_crow_magic(ctx)

    async def _tick_autorun_ready(self, ctx: WorkerCtx) -> None:
        assert ctx.db is not None and ctx.run_id is not None
        ready = compute_ready(ctx.db)
        if not ready:
            return
        command_id = str(uuid4())
        idempotency_key = f"scheduler.kickoff_ready:{ctx.run_id}:{self._tick_seq}"
        try:
            enqueue_command(
                ctx.db,
                command_id=command_id,
                run_id=ctx.run_id,
                agent_id=self.name,
                role=None,
                ticket_id=None,
                target_worker="orchestrator",
                kind="scheduler.kickoff_ready",
                payload={},
                correlation_id=command_id,
                idempotency_key=idempotency_key,
                retryable=False,
            )
        except sqlite3.IntegrityError:
            pass  # duplicate key — previous tick's command still pending

    async def _tick_crow_magic(self, ctx: WorkerCtx) -> None:
        assert ctx.db is not None and ctx.run_id is not None
        now = datetime.now(timezone.utc)

        snap_rows = ctx.db.execute(
            """
            SELECT s.harness, s.status_json
              FROM harness_usage_snapshots s
              JOIN (
                    -- Pick the single newest row per harness; the rowid tiebreak
                    -- prevents duplicate-timestamp snapshots from selecting a
                    -- harness twice in one tick (double-emit).
                    SELECT harness, MAX(rowid) AS rowid
                      FROM harness_usage_snapshots
                     WHERE fetched_at = (
                           SELECT MAX(fetched_at)
                             FROM harness_usage_snapshots AS inner_s
                            WHERE inner_s.harness = harness_usage_snapshots.harness
                       )
                     GROUP BY harness
                   ) latest
                ON latest.rowid = s.rowid
            """
        ).fetchall()

        for snap_row in snap_rows:
            harness = snap_row["harness"]
            snapshot = UsageStatusSnapshot.from_json(snap_row["status_json"])
            if snapshot is None:
                continue
            for window in snapshot.windows:
                await self._evaluate_window(ctx, harness, window, now)

        # Usage-reset detection: compare last two snapshots per harness
        await self._check_usage_reset(ctx)

    async def _check_usage_reset(self, ctx: WorkerCtx) -> None:
        assert ctx.db is not None and ctx.run_id is not None
        harnesses = ctx.db.execute(
            "SELECT DISTINCT harness FROM harness_usage_snapshots"
        ).fetchall()
        for row in harnesses:
            harness = row["harness"]
            pair = ctx.db.execute(
                """
                SELECT status_json FROM harness_usage_snapshots
                 WHERE harness = ?
                 ORDER BY fetched_at DESC, rowid DESC
                 LIMIT 2
                """,
                (harness,),
            ).fetchall()
            if len(pair) < 2:
                continue
            curr = UsageStatusSnapshot.from_json(pair[0]["status_json"])
            prev = UsageStatusSnapshot.from_json(pair[1]["status_json"])
            if curr is None or prev is None:
                continue
            curr_pct = curr.first_percent_used()
            prev_pct = prev.first_percent_used()
            if curr_pct is None or prev_pct is None:
                continue
            if usage_reset_detected(prev_pct, curr_pct):
                # Avoid double-emit: only emit if prev_pct differs from last emitted
                last = self._last_reset_prev_pct.get(harness)
                if last != prev_pct:
                    self._last_reset_prev_pct[harness] = prev_pct
                    if ctx.bus is not None:
                        await ctx.bus.publish(
                            UsageResetEvent(
                                run_id=ctx.run_id,
                                agent_id=self.name,
                                harness=harness,
                                prev_pct=prev_pct,
                                curr_pct=curr_pct,
                            )
                        )

    async def _evaluate_window(
        self,
        ctx: WorkerCtx,
        harness: str,
        window: UsageWindow,
        now: datetime,
    ) -> None:
        assert ctx.db is not None and ctx.run_id is not None

        percent_used = window.percent_used
        reset_at_str = window.reset_at
        window_key = window.window_key

        if percent_used is None or reset_at_str is None:
            return

        try:
            reset_at = datetime.fromisoformat(reset_at_str)
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            t_until_reset = (reset_at - now).total_seconds() / 60.0
        except (ValueError, TypeError):
            return

        if t_until_reset <= 0:
            return

        t_period = _parse_t_period(window)
        if t_period is None or t_period <= 0:
            return

        # RT5 steering. `_evaluate_window` is only ever called from `_tick_crow_magic`,
        # so steering is consumed in crow_magic mode only — no extra mode gate is needed.
        steering, any_prefer = self._load_steering(ctx.db, harness)

        if steering == "pause":
            # Skip the policy entirely: never call decide(), never enqueue a kickoff.
            # Still record the decision so the panel reflects the paused state.
            usage = float(percent_used) / 100.0
            visible_changed = self._upsert_decision_cache(
                ctx,
                harness,
                window_key,
                "crow_magic",
                False,
                usage,
                t_until_reset,
                t_period,
                0.0,
                "paused by user",
                None,
            )
            await self._emit_decision(
                ctx,
                harness,
                window_key,
                False,
                usage,
                t_until_reset,
                t_period,
                0.0,
                "paused by user",
                None,
                emit_queue_row=visible_changed,
            )
            return

        # prefer: NULL-harness ready tickets are reserved for preferred harnesses
        # while any prefer steering exists. A preferred harness (or any harness when
        # no prefer exists) keeps today's NULL-inclusive eligibility.
        reserve_null = any_prefer and steering != "prefer"

        # Load per-(harness, window_key) params; fall back to usage_threshold_curve defaults
        params_row = ctx.db.execute(
            "SELECT c_changeoff, t_alwaysyes, alwayscutoff, intensity, multiharness_cutoff "
            "FROM scheduler_params WHERE harness = ? AND window_key = ?",
            (harness, window_key),
        ).fetchone()

        params_obj = SchedulerParamsPayload()
        if params_row is not None:
            parsed = _coerce_scheduler_params_row(params_row)
            if parsed is not None:
                params_obj = parsed

        busy = (
            ctx.db.execute(
                "SELECT COUNT(*) AS n FROM tickets WHERE harness = ? AND status = 'in_progress'",
                (harness,),
            ).fetchone()["n"]
            > 0
        )
        harness_clause = "t.harness = ?" if reserve_null else "(t.harness = ? OR t.harness IS NULL)"
        ready_rows = ctx.db.execute(
            f"""
            SELECT t.id, t.schedule_at, t.harness, t.updated_at
              FROM tickets AS t
             WHERE t.status = 'ready'
               AND {harness_clause}
               AND NOT EXISTS (
                   SELECT 1 FROM ticket_deps AS d
                     JOIN tickets AS dep ON dep.id = d.depends_on_id
                    WHERE d.ticket_id = t.id
                      AND dep.status NOT IN ('done', 'archived')
               )
            """,
            (harness,),
        ).fetchall()
        ready_tickets = [
            TicketRecord(
                id=row["id"],
                schedule_at=row["schedule_at"],
                harness=row["harness"],
            )
            for row in ready_rows
        ]
        # ticket_id -> updated_at of its current `ready` row; folded into the
        # kickoff idempotency key so a ticket that stays `ready` (kickoff
        # enqueued but not yet flipped to in_progress) is NOT re-enqueued every
        # tick, while a ticket that cycles back to `ready` (new updated_at)
        # gets a fresh kickoff.
        ready_state_token = {row["id"]: row["updated_at"] for row in ready_rows}

        policy_input = SchedulerInput(
            window=SchedulerWindow(
                harness=harness,
                window_key=window_key,
                percent_used=float(percent_used),
                t_until_reset=t_until_reset,
                t_period=t_period,
            ),
            params=SchedulerParams(
                c_changeoff=params_obj.c_changeoff,
                t_alwaysyes=params_obj.t_alwaysyes,
                alwayscutoff=params_obj.alwayscutoff,
                intensity=params_obj.intensity,
                multiharness_cutoff=params_obj.multiharness_cutoff,
            ),
            harness_busy={harness: busy},
            provider_budgets={},
            caps=SchedulerCaps(),
            ready_tickets=ready_tickets,
        )
        decision = decide(policy_input)
        usage = float(percent_used) / 100.0
        threshold = decision.threshold_used if decision.threshold_used is not None else 0.0
        should_kick = decision.action == "kick"
        ticket_id = decision.ticket_id

        visible_changed = self._upsert_decision_cache(
            ctx,
            harness,
            window_key,
            "crow_magic",
            should_kick,
            usage,
            t_until_reset,
            t_period,
            threshold,
            decision.rationale,
            ticket_id,
        )
        await self._emit_decision(
            ctx,
            harness,
            window_key,
            should_kick,
            usage,
            t_until_reset,
            t_period,
            threshold,
            decision.rationale,
            ticket_id,
            emit_queue_row=visible_changed,
        )

        if not should_kick or ticket_id is None:
            return

        command_id = str(uuid4())
        # Key on (ticket, ready-state token) rather than tick_seq: a stuck-ready
        # ticket keeps the same key across ticks (IntegrityError → no re-enqueue),
        # but a ticket that transitions out of and back into `ready` gets a new
        # token and a fresh kickoff.
        state_token = ready_state_token.get(ticket_id, "")
        idempotency_key = (
            f"scheduler.kickoff_ready:{ctx.run_id}:{ticket_id}:{state_token}"
        )
        try:
            enqueue_command(
                ctx.db,
                command_id=command_id,
                run_id=ctx.run_id,
                agent_id=self.name,
                role=None,
                ticket_id=None,
                target_worker="orchestrator",
                kind="scheduler.kickoff_ready",
                payload={"only": ticket_id},
                correlation_id=command_id,
                idempotency_key=idempotency_key,
                retryable=False,
            )
        except sqlite3.IntegrityError:
            pass

    def _upsert_decision_cache(
        self,
        ctx: WorkerCtx,
        harness: str,
        window_key: str,
        mode: str,
        decision: bool,
        usage: float,
        t_until_reset: float,
        t_period: float,
        threshold: float,
        rationale: str,
        kicked_ticket_id: str | None,
    ) -> bool:
        """Upsert the decision cache; return True iff a *rendered* field changed.

        F11 H1: only ``decision`` / ``rationale`` / ``kicked_ticket_id`` are read
        back into ``state.schedule_snapshot`` (``_load_crow_magic`` / ``_crow_rationale``
        in ``schedule_snapshot.py``); ``usage`` / ``threshold`` / ``t_until_reset``
        are continuous, tick-by-tick values that are NOT rendered from this row (the
        usage gauges read ``harness_usage_snapshots`` instead). So we compare only the
        rendered columns against the prior row and report whether a visible change
        occurred — the caller emits the key-only ``queue_row`` invalidation ONLY then,
        bounding refetches to genuine decision flips rather than every 10s tick.
        """
        assert ctx.db is not None
        now = datetime.now(timezone.utc).isoformat()
        prior = ctx.db.execute(
            "SELECT decision, rationale, kicked_ticket_id"
            "  FROM scheduler_decision_cache WHERE harness = ? AND window_key = ?",
            (harness, window_key),
        ).fetchone()
        visible_changed = prior is None or (
            int(prior["decision"]) != int(decision)
            or str(prior["rationale"] or "") != str(rationale or "")
            or (prior["kicked_ticket_id"] or None) != (kicked_ticket_id or None)
        )
        ctx.db.execute(
            """
            INSERT INTO scheduler_decision_cache
                (harness, window_key, mode, decision, usage, t_until_reset,
                 t_period, threshold, rationale, kicked_ticket_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(harness, window_key) DO UPDATE SET
                mode             = excluded.mode,
                decision         = excluded.decision,
                usage            = excluded.usage,
                t_until_reset    = excluded.t_until_reset,
                t_period         = excluded.t_period,
                threshold        = excluded.threshold,
                rationale        = excluded.rationale,
                kicked_ticket_id = excluded.kicked_ticket_id,
                updated_at       = excluded.updated_at
            """,
            (
                harness,
                window_key,
                mode,
                int(decision),
                usage,
                t_until_reset,
                t_period,
                threshold,
                rationale,
                kicked_ticket_id,
                now,
            ),
        )
        return visible_changed

    async def _emit_decision(
        self,
        ctx: WorkerCtx,
        harness: str,
        window_key: str,
        decision: bool,
        usage: float,
        t_until_reset: float,
        t_period: float,
        threshold: float,
        rationale: str,
        kicked_ticket_id: str | None,
        emit_queue_row: bool = True,
    ) -> None:
        # Forensic capture rides the bus aspect: the SchedulerDecisionEvent below
        # carries exactly these fields and the recorder subscriber routes it into
        # decision_records, so there is no separate record_decision() call here.
        if ctx.bus is None or ctx.run_id is None:
            return
        await ctx.bus.publish(
            SchedulerDecisionEvent(
                run_id=ctx.run_id,
                agent_id=self.name,
                mode="crow_magic",
                harness=harness,
                window_key=window_key,
                decision=decision,
                usage=usage,
                t_until_reset=t_until_reset,
                t_period=t_period,
                threshold=threshold,
                rationale=rationale,
                kicked_ticket_id=kicked_ticket_id,
            )
        )
        # F1 (queue_row chunk) + F11 H1 coalescing: the rich `SchedulerDecisionEvent`
        # above is an internal detail; the CLIENT-facing invalidation is the key-only
        # `state.snapshot{queue_row}`, and Ink refetches the whole schedule slice on
        # ANY queue_row event. The crow_magic tick runs every ~10s per (harness, window)
        # and recomputes usage/threshold/t_until_reset each time, but `state.schedule_snapshot`
        # only renders `decision` / `rationale` / `kicked_ticket_id` from this cache row
        # (the usage gauges read `harness_usage_snapshots` instead). So we emit only when
        # `_upsert_decision_cache` reports a change to one of those rendered fields
        # (`emit_queue_row`), bounding refetches to genuine decision flips rather than
        # every tick. Key = `harness:window_key` (the decision-cache primary key); no
        # queue_row table exists (plan line 322). This is a thread worker with a live
        # `ctx.bus`; Runtime.emit_snapshot is unavailable here, so we await directly.
        if emit_queue_row:
            await ctx.bus.publish(
                StateSnapshotEvent(
                    run_id=ctx.run_id,
                    agent_id=self.name,
                    entity=Entity.QUEUE_ROW,
                    key=f"{harness}:{window_key}",
                )
            )

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if command.kind == self.SET_MODE:
            return await self._handle_set_mode(command, ctx)
        if command.kind == self.SET_PARAMS:
            return await self._handle_set_params(command, ctx)
        if command.kind == self.SET_STEERING:
            return await self._handle_set_steering(command, ctx)
        return {"handled": False}

    async def _handle_set_mode(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if ctx.db is None:
            raise RuntimeError("SchedulerWorker requires ctx.db")
        to_mode = command.payload.get("mode")
        if to_mode not in _VALID_MODES:
            raise ValueError(f"scheduler.set_mode: unknown mode {to_mode!r}")
        row = ctx.db.execute("SELECT mode FROM scheduler_state WHERE id = 1").fetchone()
        from_mode = row["mode"] if row else "manual"
        now = datetime.now(timezone.utc).isoformat()
        ctx.db.execute(
            "UPDATE scheduler_state SET mode = ?, updated_at = ? WHERE id = 1",
            (to_mode, now),
        )
        if ctx.bus is not None and ctx.run_id is not None:
            changed_by = command.payload.get("changed_by", "user")
            if changed_by not in {"user", "api"}:
                changed_by = "user"
            await ctx.bus.publish(
                SchedulerModeEvent(
                    run_id=ctx.run_id,
                    agent_id=self.name,
                    from_mode=from_mode,
                    to_mode=to_mode,
                    changed_by=changed_by,
                )
            )
        return {"handled": True, "from_mode": from_mode, "to_mode": to_mode}

    async def _handle_set_params(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if ctx.db is None:
            raise RuntimeError("SchedulerWorker requires ctx.db")
        try:
            payload = SchedulerSetParamsPayload.model_validate(command.payload)
        except ValidationError as exc:
            raise ValueError(f"scheduler.set_params: invalid payload: {exc}") from exc

        harness = payload.harness.strip()
        window_key = payload.window_key.strip()
        if not harness:
            raise ValueError("scheduler.set_params: harness required")
        if not window_key:
            raise ValueError("scheduler.set_params: window_key required")

        now = datetime.now(timezone.utc).isoformat()
        ctx.db.execute(
            """
            INSERT INTO scheduler_params
                (harness, window_key, c_changeoff, t_alwaysyes, alwayscutoff,
                 intensity, multiharness_cutoff, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(harness, window_key) DO UPDATE SET
                c_changeoff         = excluded.c_changeoff,
                t_alwaysyes         = excluded.t_alwaysyes,
                alwayscutoff        = excluded.alwayscutoff,
                intensity           = excluded.intensity,
                multiharness_cutoff = excluded.multiharness_cutoff,
                updated_at          = excluded.updated_at
            """,
            (
                harness,
                window_key,
                payload.params.c_changeoff,
                payload.params.t_alwaysyes,
                payload.params.alwayscutoff,
                payload.params.intensity,
                payload.params.multiharness_cutoff,
                now,
            ),
        )
        return {"handled": True, "harness": harness, "window_key": window_key}

    async def _handle_set_steering(
        self, command: CommandEvent, ctx: WorkerCtx
    ) -> dict[str, Any]:
        if ctx.db is None:
            raise RuntimeError("SchedulerWorker requires ctx.db")
        raw_harness = command.payload.get("harness")
        steering = command.payload.get("steering")
        harness = (raw_harness or "").strip() if isinstance(raw_harness, str) else ""
        if not harness:
            raise ValueError("scheduler.set_steering: harness required")
        if steering not in _VALID_STEERING:
            raise ValueError(f"scheduler.set_steering: unknown steering {steering!r}")

        now = datetime.now(timezone.utc).isoformat()
        ctx.db.execute(
            """
            INSERT INTO scheduler_steering (harness, steering, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(harness) DO UPDATE SET
                steering   = excluded.steering,
                updated_at = excluded.updated_at
            """,
            (harness, steering, now),
        )
        # Key-only client invalidation: queue_row already invalidates the Ink
        # usage slice, which refetches the schedule snapshot (carrying steering).
        if ctx.bus is not None and ctx.run_id is not None:
            await ctx.bus.publish(
                StateSnapshotEvent(
                    run_id=ctx.run_id,
                    agent_id=self.name,
                    entity=Entity.QUEUE_ROW,
                    key=f"steering:{harness}",
                )
            )
        return {"handled": True, "harness": harness, "steering": steering}

    def _load_steering(self, db: sqlite3.Connection, harness: str) -> tuple[str, bool]:
        """Return (steering_for_harness, any_prefer_exists).

        Fail-soft (locked decision): a missing row OR a value outside the valid
        set coerces to 'auto', so a malformed table can never wedge scheduling.
        """
        row = db.execute(
            "SELECT steering FROM scheduler_steering WHERE harness = ?",
            (harness,),
        ).fetchone()
        steering = row["steering"] if row is not None else "auto"
        if steering not in _VALID_STEERING:
            steering = "auto"
        any_prefer = (
            db.execute(
                "SELECT COUNT(*) AS n FROM scheduler_steering WHERE steering = 'prefer'"
            ).fetchone()["n"]
            > 0
        )
        return steering, any_prefer
