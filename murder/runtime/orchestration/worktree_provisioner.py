"""Worktree resolution concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from murder.state.storage.paths import tickets_dir
from murder.state.persistence.connection import RepoDb
from murder.state.storage.worktrees import ensure_worktree_for_branch


@dataclass(frozen=True)
class CrowWorktree:
    worktree_path: str | None
    additional_workspace_dirs: tuple[str, ...]


@dataclass(frozen=True)
class ReattachWorktree:
    repo_root: Path
    worktree_path: Path | None


@dataclass(frozen=True)
class RogueWorktree:
    cwd: Path
    resolved_worktree: Path | None


class WorktreeProvisioner:
    """Resolves the worktree/cwd each spawn path requires (verbatim move)."""

    def __init__(self, *, repo_root: Path, db: RepoDb) -> None:
        self._repo_root = repo_root
        self._db = db

    async def for_crow(self, row: dict[str, Any], harness_kind: str) -> CrowWorktree:
        worktree_name = row.get("worktree")
        worktree_path: str | None = None
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_worktree_for_branch(
                self._repo_root,
                worktree_name.strip(),
                permission_connection=self._db,
            )
            worktree_path = str(worktree.path)
        additional_workspace_dirs: tuple[str, ...] = ()
        if harness_kind == "codex" and worktree_path is not None:
            additional_workspace_dirs = (str(tickets_dir(self._repo_root).resolve()),)
        return CrowWorktree(worktree_path, additional_workspace_dirs)

    async def for_reattach(self, row: dict[str, Any]) -> ReattachWorktree:
        repo_root = self._repo_root
        worktree_path: Path | None = None
        worktree_name = row.get("worktree")
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_worktree_for_branch(
                self._repo_root,
                worktree_name.strip(),
                permission_connection=self._db,
            )
            repo_root = worktree.path
            worktree_path = worktree.path
        return ReattachWorktree(repo_root, worktree_path)

    async def for_rogue(
        self,
        worktree_branch: str | None,
        worktree_path: str | None,
    ) -> RogueWorktree:
        cwd = self._repo_root
        resolved_worktree: Path | None = None
        if isinstance(worktree_branch, str) and worktree_branch.strip():
            ref = await ensure_worktree_for_branch(
                self._repo_root,
                worktree_branch.strip(),
                permission_connection=self._db,
            )
            cwd = ref.path
            resolved_worktree = ref.path
        elif isinstance(worktree_path, str) and worktree_path.strip():
            path = Path(worktree_path.strip())
            if not path.is_absolute():
                path = self._repo_root / path
            cwd = path
            resolved_worktree = path
        return RogueWorktree(cwd, resolved_worktree)
