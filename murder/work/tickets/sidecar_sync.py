"""Ticket metadata YAML <-> DB synchronization."""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.state.persistence import tickets as dbmod
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.markdown_loop import MarkdownSyncLoop
from murder.state.storage.paths import ticket_yaml, tickets_dir
from murder.work.tickets.schema import ChecklistItem, Ticket
from murder.work.tickets.sidecar import (
    TicketMetadata,
    parse_ticket_metadata,
    render_ticket_metadata,
    ticket_metadata_hash,
)
from murder.work.tickets.status import TicketStatus

# Accept legacy `t007`, slug-style `T01-scaffold`, and numeric-prefix `01-msg-types`.
# Require at least one digit to avoid ingesting arbitrary YAML by filename.
_TICKET_ID_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]*$")
_DB_OWNED_STATUSES = {
    TicketStatus.IN_PROGRESS.value,
    TicketStatus.BLOCKED.value,
    TicketStatus.DONE.value,
    TicketStatus.FAILED.value,
}
_FILE_OWNED_STATUSES = {
    TicketStatus.DRAFT.value,
    TicketStatus.PLANNED.value,
    TicketStatus.READY.value,
}


class TicketMetadataSync(MarkdownSyncLoop):
    """Poll `.murder/tickets/*.yaml` and reconcile ticket metadata."""

    def __init__(
        self,
        repo_root: Path,
        db: sqlite3.Connection,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
    ) -> None:
        super().__init__(repo_root, poll_s=poll_s, debounce_s=debounce_s)
        self.db = db
        self._ticket_columns = self._columns("tickets")

    async def reconcile_all(self) -> None:
        root = tickets_dir(self.repo_root)
        root.mkdir(parents=True, exist_ok=True)
        self._materialize_missing_yaml()
        for path in self.scan_paths():
            await self.reconcile_file(path)

    async def reconcile_file(self, path: Path) -> None:
        self.reconcile_path(path)

    def reconcile_path(self, path: Path) -> None:  # noqa: PLR0911
        ticket_id = path.stem
        if not _TICKET_ID_RE.fullmatch(ticket_id):
            return
        file_hash = self._file_hash(path)
        row = dbmod.get_ticket(self.db, ticket_id)
        try:
            meta = parse_ticket_metadata(
                path.read_text(encoding="utf-8"),
                expected_id=ticket_id,
            )
        except Exception as exc:
            if row is not None:
                self._mark_sync_state(
                    ticket_id,
                    "parse_error",
                    parse_error=str(exc),
                    file_hash=file_hash,
                )
            return

        if row is None:
            if meta.status.value not in _FILE_OWNED_STATUSES:
                return
            now = datetime.utcnow().replace(microsecond=0)
            ticket = Ticket(
                id=ticket_id,
                title=meta.title,
                wave=meta.wave,
                status=meta.status,
                harness=meta.harness,
                model=meta.model,
                created_at=now,
                updated_at=now,
            )
            ticket.deps = list(meta.deps or [])
            ticket.skills = list(meta.skills or [])
            ticket.checklist = []
            for idx, text in enumerate(meta.checklist or []):
                ticket.checklist.append(ChecklistItem(ord=idx, text=text))
            try:
                dbmod.insert_ticket(self.db, ticket)
            except sqlite3.IntegrityError as exc:
                self._mark_orphan_parse_error(ticket_id, file_hash, str(exc))
                return
            self._set_schedule_at(ticket_id, meta.schedule_at)
            self._replace_checklist(ticket_id, list(meta.checklist or []))
            self._mark_sync_state(
                ticket_id,
                "synced",
                file_hash=file_hash,
                metadata_hash=ticket_metadata_hash(meta),
                materialized_path=str(path.relative_to(self.repo_root)),
            )
            return

        db_status = str(row["status"])
        yaml_status = meta.status.value
        if db_status in _DB_OWNED_STATUSES and yaml_status != db_status:
            self._materialize_yaml(ticket_id, dbmod.get_ticket(self.db, ticket_id))
            self._mark_sync_state(
                ticket_id,
                "conflict",
                conflict_reason=f"status is DB-owned ({db_status})",
                file_hash=self._file_hash(path),
            )
            return

        if yaml_status in _DB_OWNED_STATUSES and db_status != yaml_status:
            self._mark_sync_state(
                ticket_id,
                "parse_error",
                parse_error=f"YAML status {yaml_status!r} is DB-owned runtime state",
                file_hash=file_hash,
            )
            return

        if db_status == TicketStatus.READY.value and yaml_status == TicketStatus.PLANNED.value:
            if self._has_active_crow(ticket_id):
                self._mark_sync_state(
                    ticket_id,
                    "conflict",
                    conflict_reason="cannot demote ready ticket while crow is active",
                    file_hash=file_hash,
                )
                return

        if db_status in _FILE_OWNED_STATUSES:
            self._apply_mutable_fields(ticket_id, meta, db_status)
            self._mark_sync_state(
                ticket_id,
                "synced",
                file_hash=self._file_hash(path),
                metadata_hash=ticket_metadata_hash(meta),
                materialized_path=str(path.relative_to(self.repo_root)),
            )
            return

        # Keep file in sync with DB when runtime owns status.
        self._materialize_yaml(ticket_id, dbmod.get_ticket(self.db, ticket_id))
        self._mark_sync_state(ticket_id, "synced", file_hash=self._file_hash(path))

    def scan_paths(self) -> list[Path]:
        root = tickets_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.yaml") if p.is_file())

    def _columns(self, table: str) -> set[str]:
        rows = self.db.execute(f"PRAGMA table_info({table})").fetchall()
        return {str(r["name"]) for r in rows}

    def _materialize_missing_yaml(self) -> None:
        rows = self.db.execute("SELECT id FROM tickets ORDER BY wave, id").fetchall()
        for row in rows:
            ticket_id = str(row["id"])
            if not _TICKET_ID_RE.fullmatch(ticket_id):
                continue
            path = tickets_dir(self.repo_root) / f"{ticket_id}.yaml"
            if path.exists():
                continue
            self._materialize_yaml(ticket_id, dbmod.get_ticket(self.db, ticket_id))

    def _materialize_yaml(self, ticket_id: str, row: dict[str, Any] | None) -> None:
        if row is None:
            return
        path = ticket_yaml(self.repo_root, ticket_id)
        meta = self._meta_from_row(row)
        atomic_write_text(path, render_ticket_metadata(meta))
        file_hash = self._file_hash(path)
        self._mark_sync_state(
            ticket_id,
            "synced",
            file_hash=file_hash,
            metadata_hash=ticket_metadata_hash(meta),
            materialized_path=str(path.relative_to(self.repo_root)),
        )

    def _meta_from_row(self, row: dict[str, Any]) -> TicketMetadata:
        return TicketMetadata(
            id=str(row["id"]),
            title=str(row["title"]),
            wave=int(row["wave"]),
            status=TicketStatus(str(row["status"])),
            harness=row.get("harness"),
            model=row.get("model"),
            deps=list(row.get("deps") or []),
            skills=list(row.get("skills") or []),
            checklist=[item["text"] for item in row.get("checklist") or []],
            schedule_at=row.get("schedule_at"),
        )

    def _mark_orphan_parse_error(self, ticket_id: str, file_hash: str, parse_error: str) -> None:
        # Missing rows have nowhere to persist sync state. Leave the YAML in
        # place; a later reconcile can import it once dependencies exist.
        del ticket_id, file_hash, parse_error

    def _apply_mutable_fields(self, ticket_id: str, meta: TicketMetadata, db_status: str) -> None:
        self.db.execute(
            """
            UPDATE tickets
               SET title = ?, wave = ?, harness = ?, model = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                meta.title,
                meta.wave,
                meta.harness,
                meta.model,
                datetime.utcnow().isoformat(timespec="seconds"),
                ticket_id,
            ),
        )
        if meta.status.value != db_status:
            dbmod.update_ticket_status(self.db, ticket_id, meta.status.value)
        self._set_schedule_at(ticket_id, meta.schedule_at)
        self._replace_edges("ticket_deps", "depends_on_id", ticket_id, list(meta.deps or []))
        self._replace_edges("ticket_skills", "skill", ticket_id, list(meta.skills or []))
        self._replace_checklist(ticket_id, list(meta.checklist or []))

    def _replace_edges(self, table: str, col: str, ticket_id: str, values: list[str]) -> None:
        self.db.execute(f"DELETE FROM {table} WHERE ticket_id = ?", (ticket_id,))
        for value in values:
            self.db.execute(
                f"INSERT INTO {table}(ticket_id, {col}) VALUES (?, ?)",
                (ticket_id, value),
            )

    def _replace_checklist(self, ticket_id: str, checklist: list[str]) -> None:
        self.db.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
        for idx, text in enumerate(checklist):
            self.db.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
                (ticket_id, idx, text),
            )

    def _set_schedule_at(self, ticket_id: str, schedule_at: str | None) -> None:
        if "schedule_at" not in self._ticket_columns:
            return
        self.db.execute(
            "UPDATE tickets SET schedule_at = ?, updated_at = ? WHERE id = ?",
            (schedule_at, datetime.utcnow().isoformat(timespec="seconds"), ticket_id),
        )

    def _has_active_crow(self, ticket_id: str) -> bool:
        row = self.db.execute(
            """
            SELECT 1 FROM agents
             WHERE ticket_id = ?
               AND role IN ('crow', 'crow_handler')
               AND status IN ('running', 'idle')
             LIMIT 1
            """,
            (ticket_id,),
        ).fetchone()
        return row is not None

    def _mark_sync_state(
        self,
        ticket_id: str,
        state: str,
        *,
        parse_error: str | None = None,
        conflict_reason: str | None = None,
        file_hash: str | None = None,
        metadata_hash: str | None = None,
        materialized_path: str | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        if "metadata_sync_state" in self._ticket_columns:
            assignments.append("metadata_sync_state = ?")
            values.append(state)
        if state != "synced" and "metadata_parse_error" in self._ticket_columns:
            assignments.append("metadata_parse_error = ?")
            values.append(parse_error)
        if state != "synced" and "metadata_conflict_reason" in self._ticket_columns:
            assignments.append("metadata_conflict_reason = ?")
            values.append(conflict_reason)
        if file_hash is not None and "metadata_file_hash" in self._ticket_columns:
            assignments.append("metadata_file_hash = ?")
            values.append(file_hash)
        if "metadata_materialized_path" in self._ticket_columns and materialized_path is not None:
            assignments.append("metadata_materialized_path = ?")
            values.append(materialized_path)
        if state == "synced":
            if "metadata_parse_error" in self._ticket_columns:
                assignments.append("metadata_parse_error = NULL")
            if "metadata_conflict_reason" in self._ticket_columns:
                assignments.append("metadata_conflict_reason = NULL")
            if "metadata_last_materialized_hash" in self._ticket_columns and file_hash is not None:
                assignments.append("metadata_last_materialized_hash = ?")
                values.append(file_hash)
            if "metadata_hash" in self._ticket_columns and metadata_hash is not None:
                assignments.append("metadata_hash = ?")
                values.append(metadata_hash)
        if not assignments:
            return
        sql = f"UPDATE tickets SET {', '.join(assignments)} WHERE id = ?"
        values.append(ticket_id)
        self.db.execute(sql, tuple(values))

    def _file_hash(self, path: Path) -> str:
        with contextlib.suppress(FileNotFoundError):
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return ""


def reconcile_ticket_yaml(
    *,
    conn: sqlite3.Connection,
    repo_root: str | Path,
    ticket_id: str,
) -> None:
    """Synchronously reconcile one ticket YAML sidecar into the database."""
    root = Path(repo_root)
    TicketMetadataSync(root, conn).reconcile_path(ticket_yaml(root, ticket_id))
