"""Service-side snapshot assembly for TUI and future service clients."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path

from murder.app.service.client_api import (
    ChecklistItem,
    ConversationBlockSummary,
    ConversationsSnapshot,
    ConversationSummary,
    CrowSessionSummary,
    CrowSnapshot,
    DispatchSnapshot,
    EscalationsSnapshot,
    EscalationSummary,
    InvalidationKeys,
    NoteDisplaySnapshot,
    NotesSnapshot,
    NoteSummary,
    PlanDisplaySnapshot,
    PlansSnapshot,
    PlanSummary,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ReportSummary,
    ScheduleSnapshot,
    TicketCarveSnapshot,
    TicketDetailSnapshot,
    TicketRef,
    TicketSummary,
    UsageGaugeDrillInSnapshot,
)
from murder.app.service.schedule_snapshot import (
    build_schedule_snapshot,
    build_usage_gauge_drill_in,
)
from murder.state.persistence import tickets as ticket_store
from murder.state.persistence.schema import get_db
from murder.state.storage.paths import reports_dir
from murder.work.tickets.parser import read_ticket_md
from murder.work.tickets.status import TicketStatus


class ServiceReadModel:
    """Build immutable service snapshots from the SQLite persistence layer."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._generations: dict[str, int] = defaultdict(int)

    def get_dispatch_snapshot(self) -> DispatchSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, title, status, harness, model
                  FROM tickets
                 ORDER BY id
                """
            ).fetchall()
        tickets = tuple(
            TicketSummary(
                id=str(row["id"]),
                title=str(row["title"]),
                status=TicketStatus(str(row["status"])),
                harness=_optional_str(row["harness"]),
                model=_optional_str(row["model"]),
            )
            for row in rows
        )
        return DispatchSnapshot(
            tickets=tickets,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.dispatch),
        )

    def get_plans_snapshot(self) -> PlansSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT p.name, p.status, p.sync_state,
                       (SELECT COUNT(*) FROM plan_revisions r WHERE r.plan_name = p.name)
                           AS revisions
                  FROM plans p
                 WHERE p.status != 'superseded'
                 ORDER BY COALESCE(
                   (SELECT MAX(captured_at) FROM agent_messages
                     WHERE agent_id = 'planner-' || p.name
                       AND role IN ('user', 'assistant')),
                   p.created_at
                 ) DESC, p.name
                """
            ).fetchall()
        plans = tuple(
            PlanSummary(
                name=str(row["name"]),
                status=str(row["status"]),
                revision_count=int(row["revisions"]),
                sync_state=str(row["sync_state"]),
            )
            for row in rows
        )
        return PlansSnapshot(
            plans=plans,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.plans),
        )

    def get_notes_snapshot(self) -> NotesSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
            if "status" in cols:
                rows = conn.execute(
                    """
                    SELECT name, length(body) AS size, updated_at
                      FROM notes
                     WHERE status = 'active'
                     ORDER BY updated_at DESC, name
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT name, length(body) AS size, updated_at
                      FROM notes
                     ORDER BY updated_at DESC, name
                    """
                ).fetchall()
        notes = tuple(
            NoteSummary(
                name=str(row["name"]),
                char_count=int(row["size"]),
                updated_at=_parse_datetime(row["updated_at"]) or as_of,
            )
            for row in rows
        )
        return NotesSnapshot(
            notes=notes,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.notes),
        )

    def get_reports_snapshot(self) -> ReportsSnapshot:
        as_of = datetime.utcnow()
        root = reports_dir(self.db_path.parent.parent)
        root.mkdir(parents=True, exist_ok=True)
        reports = tuple(
            ReportSummary(
                name=path.stem,
                char_count=path.stat().st_size,
                updated_at=datetime.fromtimestamp(path.stat().st_mtime),
            )
            for path in sorted(
                root.glob("*.md"),
                key=lambda candidate: (-candidate.stat().st_mtime, candidate.name),
            )
            if path.is_file()
        )
        return ReportsSnapshot(
            reports=reports,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.reports),
        )

    def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            ticket = ticket_store.get_ticket(conn, ticket_id)
        if ticket is None:
            raise KeyError(f"ticket not found: {ticket_id}")

        prose = self._read_ticket_prose(ticket_id)
        checklist = tuple(
            ChecklistItem(text=item.text, done=item.done) for item in ticket.checklist
        )
        return TicketDetailSnapshot(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            plan_md=prose["plan"],
            working_notes_md=prose["working_notes"],
            checklist=checklist,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.ticket_detail(ticket_id)),
        )

    def get_schedule_snapshot(self) -> ScheduleSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            return build_schedule_snapshot(
                conn,
                as_of=as_of,
                invalidation_key=self.current_key(InvalidationKeys.schedule),
            )

    def get_crow_snapshot(self) -> CrowSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT a.agent_id, a.role, a.ticket_id, a.status, a.session,
                       COALESCE(a.harness, t.harness) AS harness,
                       COALESCE(a.model, t.model) AS model,
                       a.worktree_path,
                       a.started_at, a.last_heartbeat_at,
                       COALESCE(t.title, '') AS title,
                       COALESCE(t.status, '') AS ticket_status
                  FROM agents a
                  LEFT JOIN tickets t ON t.id = a.ticket_id
                 WHERE a.status NOT IN ('done', 'dead')
                 ORDER BY
                       CASE a.status
                         WHEN 'escalating' THEN 0
                         WHEN 'blocked' THEN 1
                         WHEN 'running' THEN 2
                         WHEN 'idle' THEN 3
                         WHEN 'failed' THEN 4
                         ELSE 5
                       END,
                       a.started_at DESC,
                       a.agent_id
                """
            ).fetchall()
            ticket_ids = [str(r["ticket_id"]) for r in rows if r["ticket_id"]]
            open_by_ticket: dict[str, tuple[int, int]] = {}
            if ticket_ids:
                placeholders = ",".join("?" * len(ticket_ids))
                for esc in conn.execute(
                    f"""
                    SELECT ticket_id, COUNT(*) AS n, MAX(severity) AS max_sev
                      FROM escalations
                     WHERE resolved = 0 AND ticket_id IN ({placeholders})
                     GROUP BY ticket_id
                    """,
                    ticket_ids,
                ).fetchall():
                    open_by_ticket[str(esc["ticket_id"])] = (
                        int(esc["n"]),
                        int(esc["max_sev"] or 0),
                    )
        sessions = tuple(
            CrowSessionSummary(
                agent_id=str(row["agent_id"]),
                role=str(row["role"]),
                ticket_id=_optional_str(row["ticket_id"]),
                ticket_title=str(row["title"] or ""),
                status=str(row["status"]),
                session_name=_optional_str(row["session"]),
                harness=_optional_str(row["harness"]),
                last_seen=_parse_datetime(row["last_heartbeat_at"]),
                started_at=_parse_datetime(row["started_at"]),
                ticket_status=_optional_str(row["ticket_status"]) or None,
                worktree_path=_optional_str(row["worktree_path"]),
                model=_optional_str(row["model"]),
                open_escalations=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[0],
                max_severity=open_by_ticket.get(str(row["ticket_id"] or ""), (0, 0))[1],
            )
            for row in rows
        )
        return CrowSnapshot(
            sessions=sessions,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.crows),
        )

    def get_conversations_snapshot(self) -> ConversationsSnapshot:
        """Return active conversation histories for a newly connected TUI."""
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            conv_rows = conn.execute(
                """
                SELECT conversation_id, agent_id, harness, model, harness_session_id,
                       live_state, condensed, status
                  FROM conversations
                 WHERE status = 'in_progress'
                 ORDER BY updated_at DESC, conversation_id
                """
            ).fetchall()
            block_rows = conn.execute(
                """
                SELECT conversation_id, id, ordinal, kind, payload_json, sealed,
                       service_received_at
                  FROM conversation_blocks
                 WHERE conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY conversation_id, ordinal
                """
            ).fetchall()
        blocks_by_conversation: dict[str, list[ConversationBlockSummary]] = defaultdict(list)
        for row in block_rows:
            blocks_by_conversation[str(row["conversation_id"])].append(
                ConversationBlockSummary(
                    id=int(row["id"]),
                    conversation_id=str(row["conversation_id"]),
                    ordinal=int(row["ordinal"]),
                    kind=str(row["kind"]),
                    payload=json.loads(str(row["payload_json"] or "{}")),
                    sealed=bool(row["sealed"]),
                    service_received_at=str(row["service_received_at"]),
                )
            )
        conversations = tuple(
            ConversationSummary(
                conversation_id=str(row["conversation_id"]),
                agent_id=str(row["agent_id"]),
                harness=_optional_str(row["harness"]),
                model=_optional_str(row["model"]),
                harness_session_id=_optional_str(row["harness_session_id"]),
                live_state=_optional_str(row["live_state"]),
                condensed=_optional_str(row["condensed"]),
                status=str(row["status"]),
                blocks=tuple(blocks_by_conversation[str(row["conversation_id"])]),
            )
            for row in conv_rows
        )
        return ConversationsSnapshot(
            conversations=conversations,
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.conversations),
        )

    def get_escalations_snapshot(self) -> EscalationsSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            active_rows = conn.execute(
                """
                SELECT e.id, e.ts, e.ticket_id, e.severity, e.reason, e.to_recipient,
                       e.body_path, e.resolved_at, e.source_event_id, t.status AS ticket_status
                  FROM escalations e
                  LEFT JOIN tickets t ON t.id = e.ticket_id
                 WHERE e.resolved = 0
                   AND (t.status IS NULL OR t.status != 'archived')
                 ORDER BY e.ts DESC
                """
            ).fetchall()
            history_rows = conn.execute(
                """
                SELECT e.id, e.ts, e.ticket_id, e.severity, e.reason, e.to_recipient,
                       e.body_path, e.resolved_at, e.source_event_id, t.status AS ticket_status
                  FROM escalations e
                  LEFT JOIN tickets t ON t.id = e.ticket_id
                 WHERE e.resolved_at IS NOT NULL
                 ORDER BY e.resolved_at DESC
                 LIMIT 20
                """
            ).fetchall()
        return EscalationsSnapshot(
            active=tuple(_escalation_summary_from_row(row) for row in active_rows),
            history=tuple(_escalation_summary_from_row(row) for row in history_rows),
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.escalations),
        )

    def get_plan_display(self, name: str) -> PlanDisplaySnapshot | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT materialized_path, sync_state, parse_error
                  FROM plans
                 WHERE name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        materialized_path = Path(str(row["materialized_path"]))
        path = (
            materialized_path
            if materialized_path.is_absolute()
            else self.db_path.parent.parent / materialized_path
        )
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = f"# {name}\n\nMissing materialized file: `{row['materialized_path']}`\n"
        sync_state = str(row["sync_state"])
        if sync_state == "parse_error":
            text = f"# {name}\n\nParse error: {row['parse_error']}\n\n```markdown\n{text}\n```"
        return PlanDisplaySnapshot(name=name, markdown=text)

    def get_note_display(self, name: str) -> NoteDisplaySnapshot | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT materialized_path, body
                  FROM notes
                 WHERE name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        path = self.db_path.parent / str(row["materialized_path"])
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = str(row["body"])
        return NoteDisplaySnapshot(name=name, markdown=text)

    def get_report_display(self, name: str) -> ReportDisplaySnapshot | None:
        path = reports_dir(self.db_path.parent.parent) / f"{name}.md"
        if not path.exists() or not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        return ReportDisplaySnapshot(name=name, markdown=text)

    def get_usage_gauge_drill_in(
        self,
        *,
        harness: str,
        window_key: str,
        t_period_minutes: float,
    ) -> UsageGaugeDrillInSnapshot:
        with closing(self._connect()) as conn:
            return build_usage_gauge_drill_in(
                conn,
                harness=harness,
                window_key=window_key,
                t_period_minutes=t_period_minutes,
            )

    def get_ticket_carve_snapshot(self, ticket_id: str) -> TicketCarveSnapshot | None:
        with closing(self._connect()) as conn:
            ticket = ticket_store.get_ticket(conn, ticket_id)
            if ticket is None:
                return None
            dep_rows = conn.execute(
                "SELECT id, title FROM tickets WHERE id != ? ORDER BY id",
                (ticket_id,),
            ).fetchall()
        fields: dict[str, object] = {
            "status": ticket.status.value,
            "title": ticket.title,
            "schedule_at": ticket.schedule_at,
            "harness": ticket.harness,
            "model": ticket.model,
            "deps": list(ticket.deps),
            "checklist": [
                {"text": item.text, "done": item.done} for item in ticket.checklist
            ],
        }
        return TicketCarveSnapshot(
            ticket_id=ticket_id,
            fields=fields,
            dependency_options=tuple(
                TicketRef(id=str(r["id"]), title=str(r["title"] or r["id"]))
                for r in dep_rows
            ),
        )

    def get_ticket_status(self, ticket_id: str) -> str | None:
        with closing(self._connect()) as conn:
            ticket = ticket_store.get_ticket(conn, ticket_id)
        return ticket.status.value if ticket is not None else None

    def get_notetaker_recent_entries(self, limit: int = 50) -> list[dict[str, object]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, ts, raw, cleaned, short_vers
                  FROM notes_entries
                 ORDER BY ts DESC, id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def invalidate(self, key: str) -> None:
        self._generations[key] += 1

    def current_key(self, scope: str) -> str:
        return f"{scope}-{self._generations[scope]}"

    def _connect(self) -> sqlite3.Connection:
        return get_db(self.db_path)

    def _read_ticket_prose(self, ticket_id: str) -> dict[str, str]:
        path = self.db_path.parent / "tickets" / f"{ticket_id}.md"
        if not path.exists():
            return {"plan": "", "working_notes": ""}
        sections = read_ticket_md(path)
        return {
            "plan": sections.get("plan", ""),
            "working_notes": sections.get("working_notes", ""),
        }


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _escalation_summary_from_row(row: sqlite3.Row) -> EscalationSummary:
    return EscalationSummary(
        id=int(row["id"]),
        ticket_id=_optional_str(row["ticket_id"]),
        severity=int(row["severity"]),
        reason=str(row["reason"]),
        to_recipient=str(row["to_recipient"]),
        body_path=_optional_str(row["body_path"]),
        ticket_status=_optional_str(row["ticket_status"]),
    )


__all__ = ["ServiceReadModel"]
