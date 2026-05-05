"""Runtime-owned plan file synchronization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from murder import db as dbmod
from murder.plans.parser import parse, render, write
from murder.plans.schema import Plan, PlanStatus
from murder.storage.paths import plan_md, plans_dir


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class FileSnapshot:
    mtime_ns: int
    size: int
    hash: str | None = None


class PlanSync:
    """Poll `.agents/plans/*.md` and reconcile stable edits into SQLite."""

    def __init__(
        self,
        repo_root: Path,
        db,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
    ) -> None:
        self.repo_root = repo_root
        self.db = db
        self.poll_s = poll_s
        self.debounce_s = debounce_s
        self._seen: dict[Path, FileSnapshot] = {}
        self._changed_at: dict[Path, float] = {}
        self._running = False

    async def run(self) -> None:
        self._running = True
        await self.reconcile_all()
        try:
            while self._running:
                await self.poll_once()
                await asyncio.sleep(self.poll_s)
        finally:
            self._running = False

    async def poll_once(self) -> None:
        now = asyncio.get_running_loop().time()
        for path in self._scan_paths():
            try:
                stat = path.stat()
            except FileNotFoundError:
                # The file may disappear between scan and stat; treat as deleted.
                self._seen.pop(path, None)
                self._changed_at.pop(path, None)
                continue
            old = self._seen.get(path)
            if old is None:
                self._seen[path] = FileSnapshot(stat.st_mtime_ns, stat.st_size)
                self._changed_at[path] = now
                continue
            if old.mtime_ns != stat.st_mtime_ns or old.size != stat.st_size:
                self._seen[path] = FileSnapshot(stat.st_mtime_ns, stat.st_size)
                self._changed_at[path] = now
                continue
            changed_at = self._changed_at.get(path)
            if changed_at is not None and now - changed_at >= self.debounce_s:
                await self.reconcile_file(path)
                self._changed_at.pop(path, None)

        existing = set(self._scan_paths())
        for path in list(self._seen):
            if path not in existing:
                self._seen.pop(path, None)
                self._changed_at.pop(path, None)

    async def reconcile_all(self) -> None:
        plans_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        for row in dbmod.list_plans(self.db):
            path = self.repo_root / row["materialized_path"]
            if not path.exists():
                self.materialize_row(row)
        for path in self._scan_paths():
            await self.reconcile_file(path)

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

    async def reconcile_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.repo_root))
        raw = path.read_text(encoding="utf-8")
        file_hash = content_hash(raw)
        try:
            plan = parse(raw, default_name=path.stem)
        except Exception as exc:
            row = dbmod.get_plan_row(self.db, path.stem)
            if row is not None:
                dbmod.mark_plan_sync_state(
                    self.db,
                    row["name"],
                    "parse_error",
                    file_hash=file_hash,
                    parse_error=str(exc),
                )
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
                last_materialized_hash=file_hash,
                sync_state="synced",
                create_revision=True,
                revision_source="import",
            )
            return

        last_hash = row["last_materialized_hash"]
        db_changed = bool(last_hash and row["body_hash"] != last_hash)
        file_changed = bool(last_hash and file_hash != last_hash)
        if db_changed and file_changed:
            dbmod.mark_plan_sync_state(
                self.db,
                row["name"],
                "conflict",
                file_hash=file_hash,
                conflict_reason="database and markdown changed since last materialization",
            )
            return
        if db_changed:
            self.materialize_row(row)
            return
        if file_changed or row["sync_state"] in {"parse_error", "missing_file", "conflict"}:
            dbmod.upsert_plan(
                self.db,
                plan,
                content_hash=rendered_hash,
                materialized_path=rel,
                file_hash=file_hash,
                last_materialized_hash=file_hash,
                sync_state="synced",
                create_revision=file_changed,
                revision_source="file",
            )
        elif row["file_hash"] != file_hash or row["sync_state"] != "synced":
            dbmod.mark_plan_sync_state(self.db, row["name"], "synced", file_hash=file_hash)

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
               SET body_hash = ?, last_materialized_hash = ?, updated_at = ?
             WHERE name = ?
            """,
            (h, h, datetime.utcnow().isoformat(timespec="seconds"), plan.name),
        )
        return path

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
