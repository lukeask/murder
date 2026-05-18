"""Live write-set watcher (D5 layer 1)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from watchfiles import Change, awatch

if TYPE_CHECKING:
    from murder.orchestrator import Orchestrator


class WriteSetWatcher:
    def __init__(self, repo_root: Path, orchestrator: Orchestrator) -> None:
        self.repo_root = repo_root.resolve()
        self.orchestrator = orchestrator
        self._allowed: set[Path] = set()
        self._crow_writesets: dict[str, set[Path]] = {}

    def add_crow(self, ticket_id: str, write_set: Iterable[Path]) -> None:
        self._crow_writesets[ticket_id] = {Path(p) for p in write_set}
        self._recompute_union()

    def remove_crow(self, ticket_id: str) -> None:
        self._crow_writesets.pop(ticket_id, None)
        self._recompute_union()

    def _recompute_union(self) -> None:
        u: set[Path] = set()
        for paths in self._crow_writesets.values():
            u |= paths
        self._allowed = u

    async def run(self) -> None:
        async for changes in awatch(self.repo_root):
            for change, raw_path in changes:
                if change == Change.deleted:
                    continue
                path = Path(raw_path).resolve()
                try:
                    rel = path.relative_to(self.repo_root)
                except ValueError:
                    continue
                if _is_ignored(rel):
                    continue
                rel_posix = rel.as_posix()
                allowed_str = {p.as_posix() for p in self._allowed}
                if rel_posix in allowed_str:
                    continue
                ticket_id = self._blame_active_crow()
                if ticket_id:
                    await self.orchestrator.on_writeset_violation(ticket_id, rel.as_posix())

    def _blame_active_crow(self) -> str | None:
        """Pick a running crow when multiple are active (heuristic: first)."""
        if not self._crow_writesets:
            return None
        return next(iter(self._crow_writesets.keys()), None)


def _is_ignored(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return True
    head = parts[0]
    if head in (
        ".git",
        ".murder",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".venv",
        "target",
    ):
        return True
    if head.startswith("."):
        return True
    return False
