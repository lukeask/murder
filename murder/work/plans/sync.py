"""Runtime-owned plan file synchronization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from murder.state.persistence import plans as dbmod
from murder.state.storage.markdown_loop import MarkdownSyncLoop
from murder.state.storage.paths import deprecated_plans_dir, plan_md, plans_dir
from murder.work.plans.parser import parse, render, write
from murder.work.plans.schema import Plan, PlanStatus

# (path, parse_error) -> deliver a fix-message to the owning planner agent.
ParseErrorNotifier = Callable[[Path, str], Awaitable[None]]

# (plan_name) -> emit a key-only ``state.snapshot{entity=plan}``. Injected by the
# runtime so this pure parse/DB loop never touches the bus directly; the callback
# funnels the filesystem->DB reconcile path (the PRIMARY plan writer) plus the
# rename / deprecate / parse-error mutations into F1's key-only emit. The PlanSync
# analog of TicketSync's ``on_ticket_change``. See ``Runtime.emit_snapshot``.
PlanChangeNotifier = Callable[[str], None]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PlanSync(MarkdownSyncLoop):
    """Poll `.murder/plans/*.md` and reconcile stable edits into SQLite."""

    def __init__(
        self,
        repo_root: Path,
        db,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
        parse_error_notifier: ParseErrorNotifier | None = None,
        on_plan_change: PlanChangeNotifier | None = None,
    ) -> None:
        super().__init__(repo_root, poll_s=poll_s, debounce_s=debounce_s)
        self.db = db
        self.parse_error_notifier = parse_error_notifier
        self.on_plan_change = on_plan_change
        # Suppressed during the startup/shutdown bulk pass so idle malformed
        #
        # NOTE: ``_suppress_notify`` gates ONLY the parse-error notifier (planner
        # re-prompt), NOT the key-only snapshot emit. Per the ticket precedent
        # (which emits on ``reconcile_all`` startup churn), ``on_plan_change``
        # fires regardless of suppression -- a client refetch is cheap.
        # plans don't re-prompt their planner every run; only observed
        # (debounced) edits notify.
        self._suppress_notify = False

    async def reconcile_all(self) -> None:
        plans_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        for row in dbmod.list_plans(self.db):
            path = self.repo_root / row["materialized_path"]
            if not path.exists():
                self.materialize_row(row)
        self._suppress_notify = True
        try:
            for path in self.scan_paths():
                await self.reconcile_file(path)
        finally:
            self._suppress_notify = False

    async def reconcile_name(self, name: str) -> None:
        row = dbmod.get_plan_row(self.db, name)
        if row is None:
            path = plan_md(self.repo_root, name)
            if path.exists():
                await self.reconcile_file(path)
            return
        path = self.repo_root / row["materialized_path"]
        if not path.exists():
            self.materialize_row(row)
            return
        await self.reconcile_file(path)

    def rename_plan(self, old_name: str, new_name: str) -> dict[str, object]:
        """Rename a persisted plan and its materialized markdown projection."""
        row = dbmod.get_plan_row(self.db, old_name)
        if row is None:
            raise KeyError(old_name)
        if dbmod.get_plan_row(self.db, new_name) is not None:
            raise ValueError(f"plan already exists: {new_name}")

        related = self.db.execute(
            "SELECT ticket_id FROM plan_related_tickets WHERE plan_name = ? ORDER BY ticket_id",
            (old_name,),
        ).fetchall()
        related_tickets = [str(r["ticket_id"]) for r in related]
        old_path = self.repo_root / str(row["materialized_path"])
        if old_path.exists():
            try:
                plan = parse(old_path.read_text(encoding="utf-8"), default_name=old_name)
            except Exception:
                plan = plan_from_row({**row, "_related_rows": related})
        else:
            plan = plan_from_row({**row, "_related_rows": related})
        plan.name = new_name
        plan.revisions = int(row.get("revision_count") or row.get("revisions") or 0)
        plan.related_tickets = related_tickets
        plan.updated_at = datetime.utcnow()

        new_path = plan_md(self.repo_root, new_name)
        materialized_path = str(new_path.relative_to(self.repo_root))
        rendered = render(plan)
        rendered_hash = content_hash(rendered)

        with self.db:
            dbmod.rename_plan(
                self.db,
                old_name,
                new_name,
                materialized_path=materialized_path,
            )
            write(new_path, plan)
            if old_path != new_path and old_path.exists():
                old_path.unlink()
            raw = new_path.read_text(encoding="utf-8")
            file_hash = content_hash(raw)
            self.db.execute(
                """
                UPDATE plans
                   SET status = ?, updated_at = ?, body = ?, frontmatter_json = ?,
                       body_hash = ?, file_hash = ?, materialized_path = ?,
                       sync_state = 'synced', parse_error = NULL
                 WHERE name = ?
                """,
                (
                    plan.status.value,
                    plan.updated_at.isoformat(timespec="seconds"),
                    plan.body,
                    json.dumps(plan.frontmatter, sort_keys=True, default=str),
                    rendered_hash,
                    file_hash,
                    materialized_path,
                    new_name,
                ),
            )
        # Rename moves the plan's identity: the old key leaves the plans list and
        # the new key appears. Emit a key-only snapshot for BOTH so a client that
        # had either selected refetches. (Fires after COMMIT.)
        if self.on_plan_change is not None:
            self.on_plan_change(old_name)
            self.on_plan_change(new_name)
        return dbmod.get_plan_row(self.db, new_name) or {}

    def deprecate_plan(self, name: str) -> dict[str, object]:
        """Mark a plan superseded and move its markdown out of the active plan list."""
        row = dbmod.get_plan_row(self.db, name)
        if row is None:
            raise KeyError(name)

        related = self.db.execute(
            "SELECT ticket_id FROM plan_related_tickets WHERE plan_name = ? ORDER BY ticket_id",
            (name,),
        ).fetchall()
        old_path = self.repo_root / str(row["materialized_path"])
        if old_path.exists():
            try:
                plan = parse(old_path.read_text(encoding="utf-8"), default_name=name)
            except Exception:
                plan = plan_from_row({**row, "_related_rows": related})
        else:
            plan = plan_from_row({**row, "_related_rows": related})
        plan.name = name
        plan.status = PlanStatus.SUPERSEDED
        plan.revisions = int(row.get("revision_count") or row.get("revisions") or 0)
        plan.related_tickets = [str(r["ticket_id"]) for r in related]
        plan.updated_at = datetime.utcnow()

        dest_dir = deprecated_plans_dir(self.repo_root)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"
        if dest.exists() and old_path != dest:
            i = 2
            while True:
                candidate = dest_dir / f"{name}-{i}.md"
                if not candidate.exists():
                    dest = candidate
                    break
                i += 1

        write(dest, plan)
        if old_path != dest and old_path.exists():
            old_path.unlink()
        raw = dest.read_text(encoding="utf-8")
        h = content_hash(raw)
        with self.db:
            row = dbmod.deprecate_plan(
                self.db,
                name,
                materialized_path=str(dest.relative_to(self.repo_root)),
                file_hash=h,
                body_hash=h,
                body=plan.body,
                frontmatter_json=json.dumps(
                    plan.frontmatter,
                    sort_keys=True,
                    default=str,
                ),
            )
        # Supersede drops the plan from the active list (snapshot filters
        # ``status != 'superseded'``). Emit so the client refetches and drops it.
        if self.on_plan_change is not None:
            self.on_plan_change(name)
        return row

    async def reconcile_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.repo_root))
        raw = path.read_text(encoding="utf-8")
        file_hash = content_hash(raw)
        try:
            plan = parse(raw, default_name=path.stem)
        except Exception as exc:
            row = dbmod.get_plan_row(self.db, path.stem)
            if row is None:
                row = dbmod.get_plan_row_by_materialized_path(self.db, rel)
            if row is not None:
                dbmod.mark_plan_sync_state(
                    self.db,
                    row["name"],
                    "parse_error",
                    file_hash=file_hash,
                    parse_error=str(exc),
                )
                # The row's ``sync_state`` flipped to parse_error (a visible badge
                # in the plans list), so emit even though the plan body didn't
                # ingest. Only when a row exists -- a brand-new malformed file has
                # nothing to refetch.
                if self.on_plan_change is not None:
                    self.on_plan_change(row["name"])
            if not self._suppress_notify and self.parse_error_notifier is not None:
                await self.parse_error_notifier(path, str(exc))
            return

        rendered = render(plan)
        rendered_hash = content_hash(rendered)
        row = dbmod.get_plan_row(self.db, plan.name)
        if row is None:
            dbmod.upsert_plan(
                self.db,
                plan,
                content_hash=rendered_hash,
                materialized_path=rel,
                file_hash=file_hash,
                sync_state="synced",
                create_revision=True,
                revision_source="import",
            )
            if self.on_plan_change is not None:
                self.on_plan_change(plan.name)
            return

        file_changed = row["file_hash"] != file_hash
        needs_ingest = (
            file_changed
            or row["materialized_path"] != rel
            or row["sync_state"] != "synced"
        )
        if needs_ingest:
            dbmod.upsert_plan(
                self.db,
                plan,
                content_hash=rendered_hash,
                materialized_path=rel,
                file_hash=file_hash,
                sync_state="synced",
                create_revision=(rendered_hash != row["body_hash"]),
                revision_source="file",
            )
            if self.on_plan_change is not None:
                self.on_plan_change(plan.name)

    def materialize_row(self, row: dict[str, object]) -> Path:
        related = self.db.execute(
            "SELECT ticket_id FROM plan_related_tickets WHERE plan_name = ? ORDER BY ticket_id",
            (row["name"],),
        ).fetchall()
        row = {**row, "_related_rows": related}
        plan = plan_from_row(row)
        path = self.repo_root / str(row["materialized_path"])
        plan.updated_at = datetime.utcnow()
        write(path, plan)
        raw = path.read_text(encoding="utf-8")
        h = content_hash(raw)
        dbmod.mark_plan_sync_state(self.db, plan.name, "synced", file_hash=h)
        self.db.execute(
            """
            UPDATE plans
               SET body_hash = ?, updated_at = ?
             WHERE name = ?
            """,
            (h, datetime.utcnow().isoformat(timespec="seconds"), plan.name),
        )
        return path

    def scan_paths(self) -> list[Path]:
        return self._scan_paths()

    def _scan_paths(self) -> list[Path]:
        root = plans_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())


def plan_from_row(row: dict[str, object]) -> Plan:
    return Plan(
        name=str(row["name"]),
        status=PlanStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        revisions=int(row.get("revision_count") or row.get("revisions") or 0),
        related_tickets=[
            r["ticket_id"]
            for r in row.get("_related_rows", [])  # type: ignore[union-attr]
        ],
        frontmatter=json.loads(str(row.get("frontmatter_json") or "{}")),
        body=str(row["body"]),
    )


def choose_editor(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    env_editor = os.environ.get("EDITOR")
    if env_editor:
        return env_editor
    for candidate in ("vim", "nano", "vi"):
        found = shutil.which(candidate)
        if found:
            return found
    return "vi"


async def open_editor(path: Path, editor: str) -> int:
    argv = shlex.split(editor)
    if not argv:
        argv = ["vi"]
    proc = await asyncio.create_subprocess_exec(*argv, str(path))
    return await proc.wait()
