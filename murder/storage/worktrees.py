"""Git worktree provisioning for crow execution roots."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from murder.storage.paths import worktrees_dir

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class WorktreeRef:
    branch: str
    path: Path


@dataclass(frozen=True, slots=True)
class WorktreeEntry:
    path: Path
    branch: str | None
    is_main: bool


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


def safe_branch_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("branch name is required")
    if name.startswith("-") or name.endswith(".lock") or name.endswith("/"):
        raise ValueError(f"invalid branch name: {value!r}")
    for part in name.split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"invalid branch name: {value!r}")
    return name


def crow_worktree_ref(repo_root: Path, ticket_id: str) -> WorktreeRef:
    ticket_segment = safe_branch_segment(ticket_id)
    return WorktreeRef(
        branch=f"murder/crow/{ticket_segment}",
        path=worktrees_dir(repo_root) / "crow" / ticket_segment,
    )


def named_worktree_ref(repo_root: Path, branch_name: str, *, category: str) -> WorktreeRef:
    branch = safe_branch_name(branch_name)
    segment = safe_branch_segment(branch.replace("/", "-"))
    return WorktreeRef(
        branch=branch,
        path=worktrees_dir(repo_root) / category / segment,
    )


def rogue_worktree_ref(repo_root: Path, branch_name: str) -> WorktreeRef:
    return named_worktree_ref(repo_root, branch_name, category="rogue")


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


def _parse_worktree_porcelain(text: str, repo_root: Path) -> list[WorktreeEntry]:
    entries: list[WorktreeEntry] = []
    path: Path | None = None
    branch: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if path is not None:
                entries.append(
                    WorktreeEntry(
                        path=path,
                        branch=branch,
                        is_main=path.resolve() == repo_root.resolve(),
                    )
                )
            path = None
            branch = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = Path(value)
        elif key == "branch":
            branch = value.removeprefix("refs/heads/")
    if path is not None:
        entries.append(
            WorktreeEntry(
                path=path,
                branch=branch,
                is_main=path.resolve() == repo_root.resolve(),
            )
        )
    return entries


async def list_git_worktrees(repo_root: Path) -> list[WorktreeEntry]:
    rc, out, err = await _git(repo_root, "worktree", "list", "--porcelain")
    if rc != 0:
        raise WorktreeError(err.strip() or "git worktree list failed")
    return _parse_worktree_porcelain(out, repo_root)


def list_git_worktrees_sync(repo_root: Path) -> list[WorktreeEntry]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or "git worktree list failed")
    return _parse_worktree_porcelain(result.stdout, repo_root)


def list_murder_worktrees_sync(repo_root: Path) -> list[WorktreeEntry]:
    """Return only worktrees living under .murder/worktrees/."""
    base = worktrees_dir(repo_root).resolve()
    entries = list_git_worktrees_sync(repo_root)
    result = []
    for entry in entries:
        if entry.is_main:
            continue
        try:
            entry.path.resolve().relative_to(base)
            result.append(entry)
        except ValueError:
            pass
    return result


async def ensure_worktree(repo_root: Path, ref: WorktreeRef) -> WorktreeRef:
    """Create or reuse a git worktree at ``ref.path`` on ``ref.branch``."""

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


async def ensure_crow_worktree(repo_root: Path, ticket_id: str) -> WorktreeRef:
    """Create or reuse the git worktree for a ticket's crow.

    The branch is rooted at the parent checkout's current HEAD on first
    creation. If the branch already exists, this attaches a worktree to that
    branch instead of creating a second branch.
    """

    return await ensure_worktree(repo_root, crow_worktree_ref(repo_root, ticket_id))


async def ensure_named_worktree(
    repo_root: Path,
    branch_name: str,
    *,
    category: str = "rogue",
) -> WorktreeRef:
    return await ensure_worktree(repo_root, named_worktree_ref(repo_root, branch_name, category=category))


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
    "WorktreeEntry",
    "WorktreeRef",
    "crow_worktree_ref",
    "ensure_crow_worktree",
    "ensure_named_worktree",
    "ensure_worktree",
    "list_git_worktrees",
    "list_git_worktrees_sync",
    "list_murder_worktrees_sync",
    "named_worktree_ref",
    "prune_crow_worktree",
    "prune_terminal_crow_worktree",
    "prune_worktree_path",
    "rogue_worktree_ref",
    "safe_branch_name",
    "safe_branch_segment",
]
