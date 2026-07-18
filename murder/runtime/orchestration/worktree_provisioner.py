"""Worktree resolution concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.state.storage.paths import tickets_dir
from murder.state.storage.worktrees import ensure_worktree_for_branch

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import OrchestratorHost


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

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

    async def for_crow(self, row: dict[str, Any], harness_kind: str) -> CrowWorktree:
        worktree_name = row.get("worktree")
        worktree_path: str | None = None
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_worktree_for_branch(
                self.rt.repo_root,
                worktree_name.strip(),
                permission_connection=self.rt.db,
            )
            worktree_path = str(worktree.path)
        additional_workspace_dirs: tuple[str, ...] = ()
        if harness_kind == "codex" and worktree_path is not None:
            additional_workspace_dirs = (str(tickets_dir(self.rt.repo_root).resolve()),)
        return CrowWorktree(worktree_path, additional_workspace_dirs)

    async def for_reattach(self, row: dict[str, Any]) -> ReattachWorktree:
        repo_root = self.rt.repo_root
        worktree_path: Path | None = None
        worktree_name = row.get("worktree")
        if isinstance(worktree_name, str) and worktree_name.strip():
            worktree = await ensure_worktree_for_branch(
                self.rt.repo_root,
                worktree_name.strip(),
                permission_connection=self.rt.db,
            )
            repo_root = worktree.path
            worktree_path = worktree.path
        return ReattachWorktree(repo_root, worktree_path)

    async def for_rogue(
        self,
        worktree_branch: str | None,
        worktree_path: str | None,
    ) -> RogueWorktree:
        cwd = self.rt.repo_root
        resolved_worktree: Path | None = None
        if isinstance(worktree_branch, str) and worktree_branch.strip():
            ref = await ensure_worktree_for_branch(
                self.rt.repo_root,
                worktree_branch.strip(),
                permission_connection=self.rt.db,
            )
            cwd = ref.path
            resolved_worktree = ref.path
        elif isinstance(worktree_path, str) and worktree_path.strip():
            path = Path(worktree_path.strip())
            if not path.is_absolute():
                path = self.rt.repo_root / path
            cwd = path
            resolved_worktree = path
        return RogueWorktree(cwd, resolved_worktree)
