"""Git worktree provisioning for crow execution roots."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from murder.storage.paths import worktrees_dir

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class WorktreeRef:
    branch: str
    path: Path


class WorktreeError(RuntimeError):
    """Worktree provisioning failed."""


def safe_branch_segment(value: str) -> str:
    segment = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip(".-")
    while ".." in segment:
        segment = segment.replace("..", ".")
    if segment.lower().endswith(".lock"):
        segment = f"{segment[:-5]}-lock"
    segment = segment.strip(".-")
    return segment or "agent"


def crow_worktree_ref(repo_root: Path, ticket_id: str) -> WorktreeRef:
    ticket_segment = safe_branch_segment(ticket_id)
    return WorktreeRef(
        branch=f"murder/crow/{ticket_segment}",
        path=worktrees_dir(repo_root) / "crow" / ticket_segment,
    )


async def _git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_root),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_raw, stderr_raw = await proc.communicate()
    return (
        int(proc.returncode or 0),
        stdout_raw.decode("utf-8", errors="replace"),
        stderr_raw.decode("utf-8", errors="replace"),
    )


async def ensure_crow_worktree(repo_root: Path, ticket_id: str) -> WorktreeRef:
    """Create or reuse the git worktree for a ticket's crow.

    The branch is rooted at the parent checkout's current HEAD on first
    creation. If the branch already exists, this attaches a worktree to that
    branch instead of creating a second branch.
    """

    ref = crow_worktree_ref(repo_root, ticket_id)
    if (ref.path / ".git").exists():
        return ref

    ref.path.parent.mkdir(parents=True, exist_ok=True)
    rc, _out, err = await _git(
        repo_root,
        "worktree",
        "add",
        "-b",
        ref.branch,
        str(ref.path),
        "HEAD",
    )
    if rc == 0:
        return ref

    if "already exists" in err or "a branch named" in err:
        rc, _out, err = await _git(
            repo_root,
            "worktree",
            "add",
            str(ref.path),
            ref.branch,
        )
        if rc == 0:
            return ref

    raise WorktreeError(err.strip() or f"git worktree add failed for {ref.path}")


async def prune_crow_worktree(repo_root: Path, ticket_id: str) -> bool:
    """Remove a ticket worktree when git says it is safe to remove.

    Dirty worktrees are left in place so agent changes remain inspectable.
    """

    ref = crow_worktree_ref(repo_root, ticket_id)
    return await prune_worktree_path(repo_root, ref.path)


async def prune_terminal_crow_worktree(
    conn: sqlite3.Connection,
    repo_root: Path,
    ticket_id: str,
) -> bool:
    row = conn.execute(
        """
        SELECT worktree_path
          FROM agents
         WHERE role = 'crow' AND ticket_id = ?
         ORDER BY started_at DESC
         LIMIT 1
        """,
        (ticket_id,),
    ).fetchone()
    if row is not None and row["worktree_path"]:
        return await prune_worktree_path(repo_root, row["worktree_path"])
    return await prune_crow_worktree(repo_root, ticket_id)


async def prune_worktree_path(repo_root: Path, worktree_path: str | Path) -> bool:
    path = Path(worktree_path)
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        return False
    rc, _out, _err = await _git(repo_root, "worktree", "remove", str(path))
    return rc == 0


__all__ = [
    "WorktreeError",
    "WorktreeRef",
    "crow_worktree_ref",
    "ensure_crow_worktree",
    "prune_crow_worktree",
    "prune_terminal_crow_worktree",
    "prune_worktree_path",
    "safe_branch_segment",
]
