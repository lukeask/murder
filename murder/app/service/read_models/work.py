"""Work-panel snapshot builders: plans, notes, reports, ticket detail."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from pathlib import Path

from murder.app.service.client_api import (
    ChecklistItem,
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
    TicketDetailSnapshot,
)
from murder.state.persistence import tickets as ticket_store
from murder.state.storage.paths import report_md
from murder.work.tickets.parser import read_ticket_md

from ._common import (
    ReadModelBase,
    _parse_datetime,
    _plan_parent_from_frontmatter,
    _strip_frontmatter,
)


class WorkReadModel(ReadModelBase):
    """Build plan/note/report/ticket snapshots and displays."""

    def get_plans_snapshot(self) -> PlansSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT p.name, p.status, p.sync_state, p.updated_at,
                       p.frontmatter_json, length(p.body) AS body_chars,
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
                parent=_plan_parent_from_frontmatter(row["frontmatter_json"]),
                updated_at=_parse_datetime(row["updated_at"]) or as_of,
                char_count=int(row["body_chars"] or 0),
            )
            for row in rows
        )
        return PlansSnapshot(
            plans=plans,
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.plans),
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
            invalidation_key=self.keys.current_key(InvalidationKeys.notes),
        )

    def get_reports_snapshot(self) -> ReportsSnapshot:
        as_of = datetime.utcnow()
        with closing(self._connect()) as conn:
            # Guard: reports table may not exist on a very old DB that predates
            # F5.2 schema migration (get_db does not call init_db).
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reports'"
            ).fetchone()
            if table_exists:
                rows = conn.execute(
                    """
                    SELECT name, length(body) AS size, updated_at
                      FROM reports
                     WHERE status = 'active'
                     ORDER BY updated_at DESC, name
                    """
                ).fetchall()
            else:
                rows = []
        reports = tuple(
            ReportSummary(
                name=str(row["name"]),
                char_count=int(row["size"]),
                updated_at=_parse_datetime(row["updated_at"]) or as_of,
            )
            for row in rows
        )
        return ReportsSnapshot(
            reports=reports,
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.reports),
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
            body=prose["body"],
            checklist=checklist,
            deps=tuple(ticket.deps),
            harness=ticket.harness,
            model=ticket.model,
            worktree=ticket.worktree,
            schedule_at=ticket.schedule_at,
            plan_md=prose["plan"],
            working_notes_md=prose["working_notes"],
            as_of=as_of,
            invalidation_key=self.keys.current_key(InvalidationKeys.ticket_detail(ticket_id)),
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
        path = report_md(self.db_path.parent.parent, name)
        if not path.exists() or not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
        return ReportDisplaySnapshot(name=name, markdown=text)

    def _read_ticket_prose(self, ticket_id: str) -> dict[str, str]:
        path = self.db_path.parent / "tickets" / f"{ticket_id}.md"
        if not path.exists():
            return {"plan": "", "working_notes": "", "body": ""}
        raw = path.read_text(encoding="utf-8")
        sections = read_ticket_md(path)
        return {
            "plan": sections.get("plan", ""),
            "working_notes": sections.get("working_notes", ""),
            # The frontmatter-stripped body the C8 editor renders/edits verbatim. Unlike
            # the parsed plan/working_notes split, this preserves the `# Checklist` lines
            # the editor toggles. Falls back to the whole file if no frontmatter delimiter.
            "body": _strip_frontmatter(raw),
        }
