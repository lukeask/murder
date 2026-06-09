"""Shared polling/debounce loop for markdown file synchronizers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from murder.bus.protocol import Entity


@dataclass
class FileSnapshot:
    mtime_ns: int
    size: int


class MarkdownSyncLoop(ABC):
    """Deep shared loop: poll files, debounce writes, reconcile stable edits."""

    def __init__(
        self,
        repo_root: Path,
        *,
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
        entity: "Entity | None" = None,
        on_change: "Callable[[Entity, str], Awaitable[None]] | None" = None,
    ) -> None:
        self.repo_root = repo_root
        self.poll_s = poll_s
        self.debounce_s = debounce_s
        self._seen: dict[Path, FileSnapshot] = {}
        self._changed_at: dict[Path, float] = {}
        self._running = False
        self._entity = entity
        self._on_change = on_change

    async def notify_changed(self, key: str) -> None:
        """Fire the on_change callback iff both entity and callback are set.

        Call this ONLY at points where a real change was committed (after an
        upsert/revision that actually wrote, after rename/deprecate) — never on
        a bare file-touch or when the hash matched (no loop).
        """
        if self._entity is not None and self._on_change is not None:
            await self._on_change(self._entity, key)

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
        for path in self.scan_paths():
            try:
                stat = path.stat()
            except FileNotFoundError:
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

        existing = set(self.scan_paths())
        for path in list(self._seen):
            if path not in existing:
                self._seen.pop(path, None)
                self._changed_at.pop(path, None)

    @abstractmethod
    async def reconcile_all(self) -> None:
        """Bring all tracked markdown files and DB state back into sync."""

    @abstractmethod
    async def reconcile_file(self, path: Path) -> None:
        """Reconcile one materialized markdown file."""

    @abstractmethod
    def scan_paths(self) -> list[Path]:
        """Return tracked markdown files under repo_root."""
