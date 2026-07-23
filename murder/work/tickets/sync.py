"""Unified ticket markdown <-> SQLite synchronization."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.state.persistence.tickets import invalidate_ticket_schedule
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.markdown_loop import MarkdownSyncLoop
from murder.state.storage.paths import ticket_md, tickets_dir
from murder.work.tickets.parser import ParsedTicket, TicketChecklistItem, parse_ticket
from murder.work.tickets.render import render_ticket_frontmatter
from murder.work.tickets.status import TicketStatus

# (path, parse_error) -> deliver a fix-message to the owning agent. Injected by
# the runtime; pure parsing/DB code never reaches the bus directly.
ParseErrorNotifier = Callable[[Path, str], Awaitable[None]]

# Accept legacy `t007`, slug-style `T01-scaffold`, and numeric-prefix `01-msg-types`.
# Require at least one digit to avoid importing arbitrary prose files.
_TICKET_ID_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]*$")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reconcile_ticket_md(
    *,
    conn: sqlite3.Connection,
    repo_root: str | Path,
    ticket_id: str,
) -> None:
    """Synchronously reconcile one unified ticket markdown file into the database."""
    root = Path(repo_root)
    TicketSync(root, conn).reconcile_path(ticket_md(root, ticket_id))


class TicketSync(MarkdownSyncLoop):
    """Poll `.murder/tickets/*.md` and reconcile unified ticket artifacts."""

    def __init__(
        self,
        repo_root: Path,
        db: sqlite3.Connection,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
        parse_error_notifier: ParseErrorNotifier | None = None,
    ) -> None:
        super().__init__(repo_root, poll_s=poll_s, debounce_s=debounce_s)
        self.db = db
        self.parse_error_notifier = parse_error_notifier

    async def reconcile_all(self) -> None:
        tickets_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        self._materialize_missing_md()
        # `reconcile_all` is the startup/shutdown bulk pass, not an edit-watch.
        # Suppress notification here so idle malformed files don't re-prompt the
        # owning agent every run; only `reconcile_file` (a debounced, observed
        # change) notifies.
        for path in self.scan_paths():
            self.reconcile_path(path)

    async def reconcile_file(self, path: Path) -> None:
        parse_error = self.reconcile_path(path)
        if parse_error is not None and self.parse_error_notifier is not None:
            await self.parse_error_notifier(path, parse_error)

    def reconcile_path(self, path: Path) -> str | None:
        """Reconcile one ticket `.md` into the DB.

        Returns the parse-error message if the file failed to parse this call
        (so the async edit-watch caller can re-prompt the owning agent), else
        ``None``.
        """
        ticket_id = path.stem
        if not _TICKET_ID_RE.fullmatch(ticket_id):
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._materialize_ticket_id(ticket_id)
            return None

        file_hash = content_hash(raw)
        # Warm-boot skip: byte-identical content that previously synced cleanly
        # means the DB is already current, so re-parsing and re-emitting a
        # snapshot is pure waste. A changed hash, a never-synced ticket, or a
        # prior parse-error all fall through to the full reconcile below (which
        # still emits exactly one snapshot). This is what keeps `reconcile_all`
        # from firing 65 redundant snapshots on a warm boot.
        stored_hash, stored_state = self._stored_sync_state(ticket_id)
        if stored_hash is not None and stored_hash == file_hash and stored_state == "synced":
            return None
        parsed = parse_ticket(raw, default_title=ticket_id)
        if parsed.parse_error is not None:
            if self._ticket_exists(ticket_id):
                self._mark_sync_state(
                    ticket_id,
                    "parse_error",
                    file_hash=file_hash,
                    parse_error=parsed.parse_error,
                )
            return parsed.parse_error

        rel = str(path.relative_to(self.repo_root))
        self.db.execute("BEGIN")
        try:
            if self._ticket_exists(ticket_id):
                self._update_ticket_from_parsed(ticket_id, parsed)
            else:
                self._insert_ticket_from_parsed(ticket_id, parsed)
            self._replace_deps(ticket_id, parsed.deps)
            self._sync_checklist(ticket_id, parsed.checklist)
            invalidate_ticket_schedule(conn=self.db, ticket_id=ticket_id, operation="markdown")
            self._mark_sync_state(
                ticket_id,
                "synced",
                file_hash=file_hash,
                metadata_hash=content_hash(render_ticket_frontmatter(parsed) + parsed.body),
                materialized_path=rel,
            )
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return None

    def scan_paths(self) -> list[Path]:
        root = tickets_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())

    def materialize_row(self, row: sqlite3.Row) -> Path:
        ticket_id = str(row["id"])
        path = ticket_md(self.repo_root, ticket_id)
        text = self._render_row(row)
        atomic_write_text(path, text)
        file_hash = content_hash(text)
        self._mark_sync_state(
            ticket_id,
            "synced",
            file_hash=file_hash,
            metadata_hash=file_hash,
            materialized_path=str(path.relative_to(self.repo_root)),
        )
        return path

    def _materialize_missing_md(self) -> None:
        rows = self.db.execute("SELECT * FROM tickets ORDER BY id").fetchall()
        for row in rows:
            ticket_id = str(row["id"])
            if not _TICKET_ID_RE.fullmatch(ticket_id):
                continue
            path = ticket_md(self.repo_root, ticket_id)
            if not path.exists():
                self.materialize_row(row)

    def _materialize_ticket_id(self, ticket_id: str) -> None:
        row = self.db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is not None:
            self.materialize_row(row)

    def _render_row(self, row: sqlite3.Row) -> str:
        ticket_id = str(row["id"])
        deps = [
            str(r["depends_on_id"])
            for r in self.db.execute(
                "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ? ORDER BY depends_on_id",
                (ticket_id,),
            ).fetchall()
        ]
        checklist = self.db.execute(
            "SELECT text, done FROM checklist WHERE ticket_id = ? ORDER BY ord, id",
            (ticket_id,),
        ).fetchall()
        frontmatter = render_ticket_frontmatter(
            {
                "title": row["title"],
                "deps": deps,
                "harness": row["harness"],
                "model": row["model"],
                "worktree": row["worktree"],
                "parent": row["parent_ticket_id"],
            }
        )
        body_lines = ["# Checklist"]
        for item in checklist:
            mark = "x" if int(item["done"] or 0) else " "
            body_lines.append(f"[{mark}] {item['text']}")
        return frontmatter + "\n".join(body_lines).rstrip() + "\n"

    def _ticket_exists(self, ticket_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return row is not None

    def _stored_sync_state(self, ticket_id: str) -> tuple[str | None, str | None]:
        """Return ``(metadata_file_hash, metadata_sync_state)`` for a ticket.

        Both are ``None`` when the ticket row does not exist yet, which makes
        the warm-boot skip guard fall through to the full insert path.
        """
        row = self.db.execute(
            "SELECT metadata_file_hash, metadata_sync_state FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None, None
        return row["metadata_file_hash"], row["metadata_sync_state"]

    def _insert_ticket_from_parsed(self, ticket_id: str, parsed: ParsedTicket) -> None:
        now = _now()
        self.db.execute(
            """
            INSERT INTO tickets(
                id, title, status, harness, model, worktree, parent_ticket_id,
                attempts, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                ticket_id,
                parsed.title or ticket_id,
                TicketStatus.PLANNED.value,
                parsed.harness,
                parsed.model,
                parsed.worktree,
                parsed.parent,
                now,
                now,
            ),
        )
        # Observability (advanced-logging flight recorder): a `ticket.md` first
        # appearing as a `planned` row was previously invisible. Emit a
        # structured state-mutation so carve->ingest is auditable. Zero-cost when
        # advanced logging is off (the accessor returns a no-op writer).
        from murder.observability.advanced_log import (
            StateMutationRecord,
            current_advanced_log,
        )

        current_advanced_log().record_state_mutation(
            StateMutationRecord(
                entity="ticket.ingested",
                agent_id="ticket_sync",
                ticket_id=ticket_id,
                status=TicketStatus.PLANNED.value,
                harness=parsed.harness,
                model=parsed.model,
            )
        )

    def _update_ticket_from_parsed(self, ticket_id: str, parsed: ParsedTicket) -> None:
        # The `.md` is authoritative for every agent-authored field: title,
        # harness, model, worktree, and parent_ticket_id all take the parsed
        # value unconditionally. `parent` is NOT a special case — an absent /
        # `parent: null` field legitimately nulls the column, exactly as an
        # absent `harness` nulls harness. Nothing writes parent_ticket_id behind
        # the md's back: the workflow materializer renders `parent:` INTO the
        # stage `.md` (see _write_stage_ticket) before this reconcile runs, and
        # _render_row echoes the column back out, so the round-trip is closed.
        self.db.execute(
            """
            UPDATE tickets
               SET title = ?, harness = ?, model = ?, worktree = ?,
                   parent_ticket_id = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                parsed.title or ticket_id,
                parsed.harness,
                parsed.model,
                parsed.worktree,
                parsed.parent,
                _now(),
                ticket_id,
            ),
        )

    def _replace_deps(self, ticket_id: str, deps: list[str]) -> None:
        self.db.execute("DELETE FROM ticket_deps WHERE ticket_id = ?", (ticket_id,))
        for dep in deps:
            self.db.execute(
                "INSERT OR IGNORE INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
                (ticket_id, dep),
            )

    def _sync_checklist(
        self,
        ticket_id: str,
        checklist: list[TicketChecklistItem],
    ) -> None:
        existing_rows = self.db.execute(
            "SELECT id, text, done, done_at FROM checklist WHERE ticket_id = ? ORDER BY ord, id",
            (ticket_id,),
        ).fetchall()
        by_text: dict[str, deque[sqlite3.Row]] = defaultdict(deque)
        for row in existing_rows:
            by_text[str(row["text"])].append(row)

        kept_ids: set[int] = set()
        for ord_, item in enumerate(checklist):
            existing = by_text[item.text].popleft() if by_text[item.text] else None
            done = 1 if item.done else 0
            done_at = _done_at_for(item.done, existing)
            if existing is None:
                self.db.execute(
                    """
                    INSERT INTO checklist(ticket_id, ord, text, done, done_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (ticket_id, ord_, item.text, done, done_at),
                )
                last_id = self.db.execute("SELECT last_insert_rowid() AS id").fetchone()
                kept_ids.add(int(last_id["id"]))
                continue

            item_id = int(existing["id"])
            kept_ids.add(item_id)
            self.db.execute(
                """
                UPDATE checklist
                   SET ord = ?, text = ?, done = ?, done_at = ?
                 WHERE id = ?
                """,
                (ord_, item.text, done, done_at, item_id),
            )

        if kept_ids:
            placeholders = ",".join("?" for _ in kept_ids)
            self.db.execute(
                f"DELETE FROM checklist WHERE ticket_id = ? AND id NOT IN ({placeholders})",
                (ticket_id, *kept_ids),
            )
        else:
            self.db.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))

    def _mark_sync_state(
        self,
        ticket_id: str,
        state: str,
        *,
        file_hash: str | None = None,
        metadata_hash: str | None = None,
        materialized_path: str | None = None,
        parse_error: str | None = None,
    ) -> None:
        assignments: list[str] = ["metadata_sync_state = ?"]
        values: list[Any] = [state]
        if file_hash is not None:
            assignments.append("metadata_file_hash = ?")
            values.append(file_hash)
        if metadata_hash is not None:
            assignments.append("metadata_hash = ?")
            values.append(metadata_hash)
        if materialized_path is not None:
            assignments.append("metadata_materialized_path = ?")
            values.append(materialized_path)
        if state == "synced":
            assignments.extend(
                [
                    "metadata_parse_error = NULL",
                    "metadata_conflict_reason = NULL",
                    "metadata_last_materialized_hash = ?",
                ]
            )
            values.append(file_hash)
        else:
            assignments.append("metadata_parse_error = ?")
            values.append(parse_error)
        assignments.append("updated_at = ?")
        values.append(_now())
        values.append(ticket_id)
        self.db.execute(
            f"UPDATE tickets SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )


def _done_at_for(done: bool, existing: sqlite3.Row | None) -> str | None:
    if not done:
        return None
    if existing is not None and existing["done_at"]:
        return str(existing["done_at"])
    return _now()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
