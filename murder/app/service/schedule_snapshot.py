"""Schedule / dispatch snapshot assembly (service-side SQL)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from murder.app.service.client_api import (
    CalendarRunningAgent,
    CalendarScheduledTicket,
    SchedulerDecisionSummary,
    ScheduleSnapshot,
    ScheduleTicketRow,
    UsageBurnRow,
    UsageGaugeDrillInSnapshot,
    UsageGaugeSummary,
    UsageResetEvent,
)
from murder.state.persistence.usage_status import UsageStatusSnapshot

LOGGER = logging.getLogger(__name__)

_PERIOD_MINUTES: dict[tuple[str, str], float] = {
    ("claude_code", "current_session"): 5 * 60.0,
    ("claude_code", "current_week"): 7 * 24 * 60.0,
    ("codex", "5h"): 5 * 60.0,
    ("codex", "weekly"): 7 * 24 * 60.0,
    ("cursor", "auto_composer"): 30 * 24 * 60.0,
    ("cursor", "api"): 30 * 24 * 60.0,
}
_PROVIDER_ORDER = ("claude_code", "codex", "cursor")


def build_schedule_snapshot(
    conn: sqlite3.Connection,
    *,
    as_of: datetime,
    invalidation_key: str,
) -> ScheduleSnapshot:
    mode_row = conn.execute("SELECT mode FROM scheduler_state WHERE id = 1").fetchone()
    scheduler_mode = str(mode_row["mode"]) if mode_row is not None else "manual"
    mode_rationale = _crow_rationale(conn) if scheduler_mode == "crow_magic" else ""

    decisions = tuple(
        SchedulerDecisionSummary(
            harness=str(r["harness"]),
            decision=int(r["decision"]),
            rationale=str(r["rationale"] or ""),
            kicked_ticket_id=str(r["kicked_ticket_id"]) if r["kicked_ticket_id"] else None,
        )
        for r in conn.execute(
            "SELECT harness, decision, rationale, kicked_ticket_id FROM scheduler_decision_cache"
        ).fetchall()
    )

    pending_deps_subq = """
        (
            SELECT GROUP_CONCAT(pending_dep_id, ',')
              FROM (
                    SELECT dep.id AS pending_dep_id
                      FROM ticket_deps AS d
                      JOIN tickets AS dep ON dep.id = d.depends_on_id
                     WHERE d.ticket_id = t.id
                       AND dep.status NOT IN ('done', 'archived')
                     ORDER BY dep.id
                   )
        )
    """
    active = _ticket_rows(
        conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, t.parent_ticket_id,
                   {pending_deps_subq} AS pending_dep_ids
              FROM tickets AS t
             WHERE t.status IN ('planned', 'ready', 'in_progress', 'blocked', 'failed')
             ORDER BY datetime(t.updated_at) DESC, t.id
            """
        ).fetchall()
    )
    recent_done = _ticket_rows(
        conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, t.parent_ticket_id,
                   {pending_deps_subq} AS pending_dep_ids
              FROM tickets AS t
             WHERE t.status = 'done'
             ORDER BY datetime(t.updated_at) DESC, t.id
             LIMIT 6
            """
        ).fetchall()
    )
    archived = _ticket_rows(
        conn.execute(
            f"""
            SELECT t.id, t.title, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, t.parent_ticket_id,
                   {pending_deps_subq} AS pending_dep_ids
              FROM tickets AS t
             WHERE t.status = 'archived'
             ORDER BY datetime(t.updated_at) DESC, t.id
             LIMIT 20
            """
        ).fetchall()
    )

    usage_gauges = tuple(_load_gauges(conn))
    harness_rows = conn.execute(
        "SELECT DISTINCT harness FROM harness_usage_snapshots ORDER BY harness"
    ).fetchall()
    calendar_harnesses = tuple(str(r["harness"]) for r in harness_rows) or ("default",)
    running_agents = tuple(
        CalendarRunningAgent(
            agent_id=str(r["agent_id"]),
            ticket_id=str(r["ticket_id"]),
            started_at=str(r["started_at"]),
            harness=str(r["harness"]) if r["harness"] is not None else None,
        )
        for r in conn.execute(
            """
            SELECT a.agent_id, a.ticket_id, a.started_at, t.harness
              FROM agents a
              JOIN tickets t ON t.id = a.ticket_id
             WHERE a.status = 'running'
            """
        ).fetchall()
    )
    scheduled_tickets = tuple(
        CalendarScheduledTicket(
            ticket_id=str(r["ticket_id"]),
            schedule_at=str(r["schedule_at"]),
            harness=str(r["harness"]) if r["harness"] is not None else None,
        )
        for r in conn.execute(
            """
            SELECT id AS ticket_id, schedule_at, harness
              FROM tickets
             WHERE schedule_at IS NOT NULL
               AND status IN ('planned', 'ready', 'blocked')
            """
        ).fetchall()
    )

    return ScheduleSnapshot(
        scheduler_mode=scheduler_mode,
        mode_rationale=mode_rationale,
        active_tickets=active,
        recent_done_tickets=recent_done,
        archived_tickets=archived,
        scheduler_decisions=decisions,
        usage_gauges=usage_gauges,
        calendar_harnesses=calendar_harnesses,
        running_agents=running_agents,
        scheduled_tickets=scheduled_tickets,
        as_of=as_of,
        invalidation_key=invalidation_key,
    )


def _ticket_rows(rows: list[sqlite3.Row]) -> tuple[ScheduleTicketRow, ...]:
    return tuple(
        ScheduleTicketRow(
            id=str(r["id"]),
            title=str(r["title"] or ""),
            status=str(r["status"]),
            last_update_at=_parse_ticket_updated_at(r["updated_at"]),
            last_update_label=_last_update_label(r),
            schedule_at=str(r["schedule_at"]) if r["schedule_at"] else None,
            harness=str(r["harness"]) if r["harness"] is not None else None,
            model=str(r["model"]) if r["model"] is not None else None,
            metadata_sync_state=str(r["metadata_sync_state"] or "synced"),
            metadata_parse_error=str(r["metadata_parse_error"])
            if r["metadata_parse_error"]
            else None,
            metadata_conflict_reason=str(r["metadata_conflict_reason"])
            if r["metadata_conflict_reason"]
            else None,
            pending_dep_ids=_split_pending_dep_ids(r["pending_dep_ids"]),
            parent=str(r["parent_ticket_id"]) if r["parent_ticket_id"] else None,
        )
        for r in rows
    )


def _split_pending_dep_ids(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(dep_id for dep_id in str(raw).split(",") if dep_id)


def _parse_ticket_updated_at(raw: object) -> datetime:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        LOGGER.debug(
            "ticket updated_at not ISO-parseable (raw=%r); using utcnow fallback",
            raw,
            exc_info=True,
        )
        return datetime.utcnow()


def _last_update_label(row: sqlite3.Row) -> str:
    sync_state = str(row["metadata_sync_state"] or "synced")
    if sync_state == "conflict" or row["metadata_conflict_reason"]:
        return "metadata conflict"
    if sync_state == "parse_error" or row["metadata_parse_error"]:
        return "metadata parse error"
    status = str(row["status"] or "")
    if status == "failed" and row["last_error"]:
        return "status failed"
    if status:
        return f"status {status.replace('_', ' ')}"
    return "content"


def _crow_rationale(conn: sqlite3.Connection) -> str:
    dec_rows = conn.execute(
        "SELECT harness, window_key, decision, rationale"
        " FROM scheduler_decision_cache ORDER BY updated_at DESC"
    ).fetchall()
    if not dec_rows:
        snap_n = conn.execute("SELECT COUNT(*) AS n FROM harness_usage_snapshots").fetchone()["n"]
        if snap_n == 0:
            return "no usage snapshots — press ctrl+r to fetch"
        return "evaluating…"
    holds = [r for r in dec_rows if not r["decision"]]
    kicks = [r for r in dec_rows if r["decision"]]
    if kicks:
        latest_kick = kicks[0]
        if len(dec_rows) > 1:
            return f"[{len(holds)} holding]  {latest_kick['rationale']}"
        return latest_kick["rationale"]
    if len(holds) == 1:
        return holds[0]["rationale"]
    labels = " · ".join(f"{r['harness']}/{r['window_key']}" for r in holds)
    return f"holding: {labels}"


def _load_gauges(conn: sqlite3.Connection) -> list[UsageGaugeSummary]:
    snap_rows = conn.execute(
        """
        SELECT s.harness, s.status_json
          FROM harness_usage_snapshots s
          JOIN (
                SELECT harness, MAX(fetched_at) AS fetched_at
                  FROM harness_usage_snapshots
                 GROUP BY harness
               ) latest
            ON latest.harness = s.harness
           AND latest.fetched_at = s.fetched_at
         ORDER BY s.harness
        """
    ).fetchall()
    # RT5: per-harness steering (auto/pause/prefer). Loaded once; coerce any
    # unknown value to 'auto' (fail-soft, matching the worker's _load_steering).
    steering_map: dict[str, str] = {}
    for s_row in conn.execute("SELECT harness, steering FROM scheduler_steering").fetchall():
        val = str(s_row["steering"])
        steering_map[str(s_row["harness"])] = val if val in {"auto", "pause", "prefer"} else "auto"
    now = datetime.now(timezone.utc)
    gauges: list[UsageGaugeSummary] = []
    for snap_row in snap_rows:
        harness = str(snap_row["harness"])
        snapshot = UsageStatusSnapshot.from_json(snap_row["status_json"])
        if snapshot is None:
            continue
        for window in snapshot.windows:
            pct = window.percent_used
            if pct is None:
                continue
            window_key = window.window_key
            reset_at_str = window.reset_at or window.ends_at
            t_until = 0.0
            if reset_at_str:
                try:
                    reset_at = datetime.fromisoformat(str(reset_at_str))
                    if reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=timezone.utc)
                    t_until = max(0.0, (reset_at - now).total_seconds() / 60.0)
                except (ValueError, TypeError):
                    LOGGER.debug(
                        "usage gauge reset_at not ISO-parseable for %s/%s (raw=%r);"
                        " treating as already-reset",
                        harness,
                        window_key,
                        reset_at_str,
                        exc_info=True,
                    )
            t_period = _PERIOD_MINUTES.get((harness, window_key), 0.0)
            gauges.append(
                UsageGaugeSummary(
                    harness=harness,
                    window_key=window_key,
                    pct=float(pct),
                    t_until_reset_minutes=t_until,
                    t_period_minutes=t_period,
                    steering=steering_map.get(harness, "auto"),
                )
            )
    order = {h: i for i, h in enumerate(_PROVIDER_ORDER)}
    gauges.sort(key=lambda g: order.get(g.harness, len(order)))
    return gauges


_SPARK_BARS = "▁▂▃▄▅▆▇█"


def build_usage_gauge_drill_in(
    conn: sqlite3.Connection,
    *,
    harness: str,
    window_key: str,
    t_period_minutes: float,
) -> UsageGaugeDrillInSnapshot:
    return UsageGaugeDrillInSnapshot(
        harness=harness,
        window_key=window_key,
        sparkline=_spark_history(conn, harness, window_key),
        recent_resets=tuple(_recent_reset_events(conn, harness, window_key)),
        burn_rows=tuple(_burn_attribution(conn, harness, t_period_minutes)),
    )


def _spark_history(conn: sqlite3.Connection, harness: str, window_key: str) -> str:
    rows = conn.execute(
        """
        SELECT date(fetched_at) AS day, status_json
          FROM harness_usage_snapshots
         WHERE harness = ?
           AND fetched_at >= datetime('now', '-14 days')
         ORDER BY fetched_at
        """,
        (harness,),
    ).fetchall()
    by_day: dict[str, list[float]] = {}
    for row in rows:
        day = str(row["day"])
        snapshot = UsageStatusSnapshot.from_json(row["status_json"])
        if snapshot is None:
            continue
        pct = snapshot.percent_for(window_key)
        if pct is not None:
            by_day.setdefault(day, []).append(pct)
    if not by_day:
        return "(no history)"
    today = datetime.now(timezone.utc).date()
    spark: list[str] = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        pcts = by_day.get(d, [])
        avg = sum(pcts) / len(pcts) if pcts else 0.0
        idx = min(int(avg / 100.0 * 8), 7)
        spark.append(_SPARK_BARS[idx])
    return "".join(spark)


def _recent_reset_events(
    conn: sqlite3.Connection, harness: str, window_key: str, days: int = 14
) -> list[UsageResetEvent]:
    rows = conn.execute(
        """
        SELECT fetched_at, status_json FROM harness_usage_snapshots
         WHERE harness = ?
           AND fetched_at >= datetime('now', '-' || ? || ' days')
         ORDER BY fetched_at ASC
        """,
        (harness, days),
    ).fetchall()
    resets: list[UsageResetEvent] = []
    for i in range(1, len(rows)):
        curr = UsageStatusSnapshot.from_json(rows[i]["status_json"])
        prev = UsageStatusSnapshot.from_json(rows[i - 1]["status_json"])
        if curr is None or prev is None:
            continue
        curr_pct = curr.percent_for(window_key)
        prev_pct = prev.percent_for(window_key)
        if curr_pct is None or prev_pct is None:
            continue
        if prev_pct >= 30.0 and curr_pct <= 5.0:
            resets.append(
                UsageResetEvent(
                    reset_at=str(rows[i]["fetched_at"]),
                    peak_pct=prev_pct,
                )
            )
    return resets


_BURN_SQL = """
SELECT
    t.id,
    t.title,
    CAST(
        (JULIANDAY(COALESCE(a.last_heartbeat_at, ?))
         - JULIANDAY(
             CASE WHEN a.started_at > ? THEN a.started_at ELSE ? END
         )) * 1440 AS INTEGER
    ) AS active_minutes
  FROM agents a
  JOIN tickets t ON t.id = a.ticket_id
 WHERE t.harness = ?
   AND COALESCE(a.last_heartbeat_at, ?) > ?
GROUP BY t.id, t.title
HAVING active_minutes > 0
ORDER BY active_minutes DESC
LIMIT 10
"""


def _burn_attribution(
    conn: sqlite3.Connection, harness: str, t_period_minutes: float
) -> list[UsageBurnRow]:
    if t_period_minutes <= 0:
        return []
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(minutes=t_period_minutes)).isoformat()
    now_str = now.isoformat()
    rows = conn.execute(
        _BURN_SQL,
        (now_str, window_start, window_start, harness, now_str, window_start),
    ).fetchall()
    return [
        UsageBurnRow(
            ticket_id=str(r["id"]),
            title=str(r["title"]),
            active_minutes=int(r["active_minutes"]),
        )
        for r in rows
    ]
