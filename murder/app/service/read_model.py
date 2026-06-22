"""Service-side snapshot assembly for TUI and future service clients."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from murder.app.service.client_api import (
    ChecklistItem,
    ConversationBlockSummary,
    ConversationChunkSummary,
    ConversationsSnapshot,
    ConversationSummary,
    CrowSessionSummary,
    CrowSnapshot,
    HistoryItemSummary,
    HistorySnapshot,
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
    TicketDetailSnapshot,
)
from murder.app.service.schedule_snapshot import build_schedule_snapshot
from murder.state.persistence import history as history_store
from murder.state.persistence import tickets as ticket_store
from murder.state.persistence.schema import get_db
from murder.state.storage.git_transit import TransitSnapshot, build_transit_snapshot
from murder.state.storage.paths import report_md
from murder.work.tickets.parser import read_ticket_md

LOGGER = logging.getLogger(__name__)

# Ticket states that indicate the work item is closed; a failed agent on such a
# ticket is droppable once its heartbeat goes stale.
TERMINAL_TICKET_STATUSES = frozenset({"done", "failed"})

# Hide failed agents after this long without a recent heartbeat.
FAILED_STALE_AFTER = timedelta(hours=2)

# A still-OPEN user intention older than this (and not explicitly dismissed) is
# surfaced as STALE — the zero-LLM "fell through the cracks" radar. v0 taxonomy.
STALE_AFTER_HOURS = 48

# The harness kind whose graceful-exit sessions can be resumed (/resume keybind,
# built on this DTO's resumability triple). Mirrors ClaudeCodeAdapter.kind.
RESUMABLE_HARNESS = "claude_code"


def _keep_failed_session(session: CrowSessionSummary, *, now: datetime) -> bool:
    """Whether a failed agent should remain on the wire roster.

    Roster predicate: keep failed agents whose ticket is still active, or
    whose heartbeat is recent; drop the rest. ``now`` and the
    session timestamps are all naive UTC (see ``datetime.utcnow``), so they are
    compared directly without tz normalisation.
    """
    if session.status != "failed":
        return True
    ticket_status = session.ticket_status or ""
    if ticket_status and ticket_status not in TERMINAL_TICKET_STATUSES:
        return True
    last_seen = session.last_seen or session.started_at
    if last_seen is None:
        return True
    return now - last_seen <= FAILED_STALE_AFTER


class ServiceReadModel:
    """Build immutable service snapshots from the SQLite persistence layer."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._generations: dict[str, int] = defaultdict(int)

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
            invalidation_key=self.current_key(InvalidationKeys.reports),
        )

    def get_history_snapshot(self) -> HistorySnapshot:
        """Build the user-intention history feed.

        The spine is the durable ``conversation_blocks kind='user'`` record
        (written at the send boundary). Each row is joined against its
        conversation (for harness/session/status) and the ``history_status``
        overlay, then a zero-LLM status is derived per row. The view filters
        (loose-threads vs all) and orders client-side; this returns the full,
        noise-filtered feed in newest-first order with derived state.
        """
        as_of = datetime.utcnow()
        stale_before = as_of - timedelta(hours=STALE_AFTER_HOURS)
        with closing(self._connect()) as conn:
            overlay = history_store.get_status_map(conn)
            rows = conn.execute(
                """
                SELECT b.conversation_id, b.ordinal, b.payload_json,
                       b.service_received_at,
                       c.agent_id, c.harness, c.harness_session_id,
                       c.status AS conversation_status
                  FROM conversation_blocks b
                  JOIN conversations c
                    ON c.conversation_id = b.conversation_id
                 WHERE b.kind = 'user'
                 ORDER BY b.service_received_at DESC, b.conversation_id, b.ordinal DESC
                """
            ).fetchall()
        items: list[HistoryItemSummary] = []
        for row in rows:
            text = _extract_user_text(row["payload_json"])
            if _is_noise(text):
                continue
            item_id = f"{row['conversation_id']}:{int(row['ordinal'])}"
            ts = str(row["service_received_at"])
            overlay_row = overlay.get(item_id)
            if overlay_row is not None and overlay_row[0] == "dismissed":
                status = "dismissed"
            elif _is_stale(ts, stale_before):
                status = "stale"
            else:
                status = "open"
            harness = _optional_str(row["harness"])
            conversation_status = str(row["conversation_status"])
            resumable = (
                harness == RESUMABLE_HARNESS
                and conversation_status == "complete"
                and bool(row["harness_session_id"])
            )
            items.append(
                HistoryItemSummary(
                    item_id=item_id,
                    text=text,
                    target=str(row["agent_id"]),
                    ts=ts,
                    status=status,
                    harness=harness,
                    conversation_status=conversation_status,
                    resumable=resumable,
                )
            )
        return HistorySnapshot(
            items=tuple(items),
            as_of=as_of,
            invalidation_key=self.current_key(InvalidationKeys.history),
        )

    def get_transit_snapshot(self) -> TransitSnapshot:
        """Build the per-lane git commit-graph for the Transit panel.

        Derived from git on demand (``main`` + ``.murder/worktrees`` branches),
        not persisted. ``repo_root`` is recovered from ``db_path`` (which is
        ``<repo_root>/.murder/murder.db``). The fingerprint doubles as the
        ``invalidation_key`` so the poll loop's change detection and the
        client's refetch keying agree.
        """
        repo_root = self.db_path.parent.parent
        return build_transit_snapshot(repo_root)

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
                ticket_id=_optional_str(row["ticket_id"]) or None,
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
        # done/dead are excluded in SQL; drop stale failed agents here so the
        # wire roster never carries them (Ink does no client-side filtering).
        sessions = tuple(s for s in sessions if _keep_failed_session(s, now=as_of))
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
                       live_state, queued_message, status
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
            summary_rows = conn.execute(
                """
                SELECT conversation_id, summary_id, chunk_idx, summary
                  FROM conversation_chunk_summaries
                 WHERE conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY conversation_id, chunk_idx
                """
            ).fetchall()
            summary_block_rows = conn.execute(
                """
                SELECT csb.summary_id AS summary_id, csb.block_id AS block_id
                  FROM chunk_summary_blocks csb
                  JOIN conversation_chunk_summaries ccs
                    ON ccs.summary_id = csb.summary_id
                 WHERE ccs.conversation_id IN (
                       SELECT conversation_id
                         FROM conversations
                        WHERE status = 'in_progress'
                  )
                 ORDER BY csb.summary_id, csb.block_id
                """
            ).fetchall()
        block_ids_by_summary: dict[int, list[int]] = defaultdict(list)
        for row in summary_block_rows:
            block_ids_by_summary[int(row["summary_id"])].append(int(row["block_id"]))
        summaries_by_conversation: dict[str, list[ConversationChunkSummary]] = defaultdict(list)
        for row in summary_rows:
            summaries_by_conversation[str(row["conversation_id"])].append(
                ConversationChunkSummary(
                    summary_id=int(row["summary_id"]),
                    chunk_idx=int(row["chunk_idx"]),
                    summary=str(row["summary"]),
                    block_ids=tuple(block_ids_by_summary.get(int(row["summary_id"]), ())),
                )
            )
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
                chunk_summaries=tuple(summaries_by_conversation[str(row["conversation_id"])]),
                queued_message=_optional_str(row["queued_message"]),
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

    def get_harness_models_snapshot(self) -> dict[str, object]:
        """Return the locked RPC payload for ``state.harness_models_snapshot``.

        Shape (wrapped by ``_value(...)`` in the host)::

            {
              "models": {
                "<harness_kind>": [{"id": "...", "label": "..."}, ...],
                ...
              },
              "as_of": "<ISO8601 UTC string>" | null
            }

        *as_of* is the most-recent ``fetched_at`` across all rows (null when
        the table is empty or does not yet exist). Only harnesses that have
        been persisted appear as keys; a missing key is valid — the frontend
        falls back to the classvar default.
        """
        import json as _json

        with closing(self._connect()) as conn:
            # Guard: table may not exist on an old DB (idempotent CREATE TABLE
            # runs at init_db, but get_db does not call init_db).
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='harness_models'"
            ).fetchone()
            if not table_exists:
                return {"models": {}, "as_of": None}
            rows = conn.execute(
                "SELECT harness, fetched_at, models_json FROM harness_models"
            ).fetchall()

        if not rows:
            return {"models": {}, "as_of": None}

        models_map: dict[str, list[dict[str, str]]] = {}
        fetched_timestamps: list[str] = []

        for row in rows:
            harness = str(row["harness"])
            fetched_timestamps.append(str(row["fetched_at"]))
            try:
                models = _json.loads(str(row["models_json"] or "[]"))
            except (ValueError, TypeError):
                LOGGER.debug("harness_models row %r has unparseable models_json", harness)
                models = []
            models_map[harness] = models

        as_of = max(fetched_timestamps) if fetched_timestamps else None
        return {"models": models_map, "as_of": as_of}

    def invalidate(self, key: str) -> None:
        self._generations[key] += 1

    def current_key(self, scope: str) -> str:
        return f"{scope}-{self._generations[scope]}"

    def _connect(self) -> sqlite3.Connection:
        return get_db(self.db_path)

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


def _plan_parent_from_frontmatter(frontmatter_json: object) -> str | None:
    """Extract a plan's parent-plan name from its persisted frontmatter.

    The plans table holds no dedicated parent column; the only non-derived parent
    metadata is a `parent` key in the plan's frontmatter (C11 expects the parent
    plan's NAME or null). Returns None when absent, blank, or non-string.
    """
    if not isinstance(frontmatter_json, str) or not frontmatter_json:
        return None
    try:
        data = json.loads(frontmatter_json)
    except (ValueError, TypeError):
        LOGGER.debug("plan frontmatter_json failed to parse; treating parent as None")
        return None
    if not isinstance(data, dict):
        return None
    parent = data.get("parent")
    if isinstance(parent, str) and parent.strip():
        return parent.strip()
    return None


_FRONTMATTER_DELIM = "---"


def _strip_frontmatter(md_text: str) -> str:
    """Return the ticket body with leading YAML frontmatter removed.

    Mirrors ``murder.work.tickets.parser._split_frontmatter`` so the C8 editor
    receives exactly the frontmatter-stripped body (preserving the ``# Checklist``
    section). Falls back to the whole text when there is no valid frontmatter block.
    """
    if not md_text.startswith(f"{_FRONTMATTER_DELIM}\n"):
        return md_text
    try:
        _front, body = md_text[4:].split(f"\n{_FRONTMATTER_DELIM}", 1)
    except ValueError:
        return md_text
    if body.startswith("\n"):
        body = body[1:]
    return body


def _extract_user_text(payload_json: object) -> str:
    """Extract the user turn's text from a stored block payload.

    User blocks are stored as ``{"type": "user", "text": ...}`` (see
    ``conversation.append_user_message``). Returns the stripped text, or the
    empty string if the payload is malformed or has no text.
    """
    if not isinstance(payload_json, str) or not payload_json:
        return ""
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        LOGGER.debug("user-block payload_json failed to parse; returning empty text")
        return ""
    if not isinstance(data, dict):
        return ""
    text = data.get("text")
    return text.strip() if isinstance(text, str) else ""


def _is_noise(text: str) -> bool:
    """Whether a user line is command-ish noise the feed should drop.

    Skips empty/whitespace lines and command-ish lines (leading ``!`` or ``:``).
    Keeps ``@…`` lines — those are intentions aimed at a target, the feed's whole
    point. Mirrors the plan's server-side noise filter.
    """
    if not text:
        return True
    return text[0] in ("!", ":")


def _is_stale(ts: str, stale_before: datetime) -> bool:
    """Whether a user block's timestamp is older than the stale cutoff.

    A malformed/missing timestamp is treated as NOT stale (better to surface an
    OPEN item than to hide it as stale on a parse failure).
    """
    parsed = _parse_datetime(ts)
    if parsed is None:
        return False
    return parsed < stale_before


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


__all__ = ["ServiceReadModel"]
