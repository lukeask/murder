"""Runtime-owned ticket file synchronization.

Imports orphan `.murder/tickets/*.md` files into the tickets table so
externally written ticket files appear in DB-backed views.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from murder.persistence import tickets as dbmod
from murder.storage.markdown_loop import MarkdownSyncLoop
from murder.storage.paths import tickets_dir
from murder.tickets.schema import Ticket
from murder.tickets.status import TicketStatus

# Accept legacy `t007`, slug-style `T01-scaffold`, and numeric-prefix `01-msg-types`.
# Require at least one digit to avoid importing arbitrary prose files.
_TICKET_ID_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]*$")


def _clean_title_line(line: str) -> str:
    text = line.strip()
    if not text or text == "_(empty)_":
        return ""
    text = re.sub(r"^\s*#+\s*", "", text)
    text = re.sub(r"^\s*(?:[-*+]\s+|\d+\.\s+)", "", text)
    text = text.strip("`*_> ").strip()
    if not text:
        return ""
    return " ".join(text.split())


def _infer_title(path: Path, ticket_id: str) -> str:
    section_headers = {"plan", "working notes"}
    with contextlib.suppress(FileNotFoundError):
        raw = path.read_text(encoding="utf-8")
        for line in raw.splitlines():
            title = _clean_title_line(line)
            if title and title.lower() not in section_headers:
                return title[:200]
    return f"Imported {ticket_id}"


class TicketSync(MarkdownSyncLoop):
    """Poll `.murder/tickets/*.md` and ensure missing DB ticket rows exist."""

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

    async def reconcile_all(self) -> None:
        tickets_dir(self.repo_root).mkdir(parents=True, exist_ok=True)
        for path in self.scan_paths():
            await self.reconcile_file(path)

    async def reconcile_file(self, path: Path) -> None:
        ticket_id = path.stem
        if not _TICKET_ID_RE.fullmatch(ticket_id):
            return
        if dbmod.get_ticket(self.db, ticket_id) is not None:
            return

        row = self.db.execute("SELECT MAX(wave) AS wave FROM tickets").fetchone()
        default_wave = int(row["wave"]) if row and row["wave"] is not None else 1
        now = datetime.utcnow().replace(microsecond=0)
        ticket = Ticket(
            id=ticket_id,
            title=_infer_title(path, ticket_id),
            wave=max(default_wave, 1),
            status=TicketStatus.PLANNED,
            created_at=now,
            updated_at=now,
        )
        try:
            dbmod.insert_ticket(self.db, ticket)
        except sqlite3.IntegrityError:
            # Another sync tick or code path inserted it first.
            return

    def scan_paths(self) -> list[Path]:
        root = tickets_dir(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())
