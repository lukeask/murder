"""Schedule / dispatch snapshot assembly (service-side SQL)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from murder.app.service.client_api import (
    CalendarRunningAgent,
    CalendarScheduledTicket,
    ScheduleSnapshot,
    ScheduleTicketRow,
    SchedulerDecisionSummary,
    UsageBurnRow,
    UsageGaugeDrillInSnapshot,
    UsageGaugeSummary,
    UsageResetEvent,
)

_PERIOD_MINUTES: dict[tuple[str, str], float] = {
    ("claude_code", "current_session"): 5 * 60.0,
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

    dep_subq = """
        NOT EXISTS (
            SELECT 1 FROM ticket_deps AS d
              JOIN tickets AS dep ON dep.id = d.depends_on_id
             WHERE d.ticket_id = t.id
               AND dep.status NOT IN ('done', 'archived')
        )
    """
    active = _ticket_rows(
        conn.execute(
            f"""
            SELECT t.id, t.title, t.wave, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, {dep_subq} AS deps_ok
              FROM tickets AS t
             WHERE t.status IN ('planned', 'ready', 'in_progress', 'blocked', 'failed')
             ORDER BY datetime(t.updated_at) DESC, t.id
            """
        ).fetchall()
    )
    recent_done = _ticket_rows(
        conn.execute(
            f"""
            SELECT t.id, t.title, t.wave, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, {dep_subq} AS deps_ok
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
            SELECT t.id, t.title, t.wave, t.status, t.updated_at, t.schedule_at,
                   t.harness, t.model, t.last_error,
                   t.metadata_sync_state, t.metadata_parse_error,
                   t.metadata_conflict_reason, {dep_subq} AS deps_ok
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
            wave=int(r["wave"]),
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
            deps_ok=bool(int(r["deps_ok"])),
        )
        for r in rows
    )


def _parse_ticket_updated_at(raw: object) -> datetime:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
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
    now = datetime.now(timezone.utc)
    gauges: list[UsageGaugeSummary] = []
    for snap_row in snap_rows:
        harness = str(snap_row["harness"])
        try:
            payload = json.loads(snap_row["status_json"])
        except (TypeError, ValueError):
            continue
        for window in payload.get("windows") or []:
            if not isinstance(window, dict):
                continue
            pct = window.get("percent_used")
            if not isinstance(pct, (int, float)):
                continue
            window_key = str(window.get("name") or "usage")
            reset_at_str = window.get("reset_at") or window.get("ends_at")
            t_until = 0.0
            if reset_at_str:
                try:
                    reset_at = datetime.fromisoformat(str(reset_at_str))
                    if reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=timezone.utc)
                    t_until = max(0.0, (reset_at - now).total_seconds() / 60.0)
                except (ValueError, TypeError):
                    pass
            t_period = _PERIOD_MINUTES.get((harness, window_key), 0.0)
            gauges.append(
                UsageGaugeSummary(
                    harness=harness,
                    window_key=window_key,
                    pct=float(pct),
                    t_until_reset_minutes=t_until,
                    t_period_minutes=t_period,
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


def _pct_from_window(payload: dict[str, object], window_key: str) -> float | None:
    for w in payload.get("windows") or []:
        if not isinstance(w, dict):
            continue
        if (w.get("name") or "usage") == window_key:
            pct = w.get("percent_used")
            if isinstance(pct, (int, float)):
                return float(pct)
    return None


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
        try:
            payload = json.loads(row["status_json"])
        except (TypeError, ValueError):
            continue
        pct = _pct_from_window(payload, window_key)
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
        try:
            curr = json.loads(rows[i]["status_json"])
            prev = json.loads(rows[i - 1]["status_json"])
        except (TypeError, ValueError):
            continue
        curr_pct = _pct_from_window(curr, window_key)
        prev_pct = _pct_from_window(prev, window_key)
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
