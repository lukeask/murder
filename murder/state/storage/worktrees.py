"""Git worktree provisioning for crow execution roots."""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from murder.state.storage.paths import worktrees_dir

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
    """Worktree provisioning failed.

    Non-retryable: a worktree failure is a deterministic, well-defined
    condition (branch mismatch, missing branch, dirty collision), so retrying
    the same spawn would just fail the same way 90 seconds later. The supervisor
    reads this attribute to short-circuit the retry/escalation loop.
    """

    retryable = False


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


def worktree_ref(repo_root: Path, branch_name: str) -> WorktreeRef:
    """Resolve the flat worktree path for ``branch_name``.

    Every worktree lives directly at ``.murder/worktrees/<branch-slug>`` — no
    per-kind subdirectory. Asking for ``x`` gets you ``.murder/worktrees/x``.
    """

    branch = safe_branch_name(branch_name)
    segment = safe_branch_segment(branch.replace("/", "-"))
    return WorktreeRef(
        branch=branch,
        path=worktrees_dir(repo_root) / segment,
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


async def _branch_at_path(repo_root: Path, path: Path) -> str | None:
    """Return the branch checked out at ``path``, or None if git doesn't know it."""

    target = path.resolve()
    for entry in await list_git_worktrees(repo_root):
        if entry.path.resolve() == target:
            return entry.branch
    return None


async def ensure_worktree(repo_root: Path, ref: WorktreeRef) -> WorktreeRef:
    """Create or reuse a git worktree at ``ref.path`` on ``ref.branch``."""

    if (ref.path / ".git").exists():
        # Reuse only when the existing checkout is actually on the requested
        # branch. Paths are flat (``worktrees/<slug>``), so two distinct branch
        # names that slugify to the same segment collide here. Without this
        # guard the first caller to win a slug would silently lend its checkout
        # to the next, running that agent on the WRONG branch with no error.
        existing = await _branch_at_path(repo_root, ref.path)
        if existing == ref.branch:
            return ref
        raise WorktreeError(
            f"worktree at {ref.path} is checked out on {existing or 'an unknown ref'!r}, "
            f"not {ref.branch!r} (two branch names may slugify to the same path). "
            f"Resolve with one of:\n"
            f"  git worktree list\n"
            f"  git -C {ref.path} checkout {ref.branch}\n"
            f"  git worktree remove {ref.path}"
        )

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


async def ensure_worktree_for_branch(repo_root: Path, branch_name: str) -> WorktreeRef:
    """Create or reuse the flat worktree for ``branch_name``.

    The branch is rooted at the parent checkout's current HEAD on first
    creation. If the branch already exists, this attaches a worktree to that
    branch instead of creating a second branch.
    """

    return await ensure_worktree(repo_root, worktree_ref(repo_root, branch_name))


async def prune_terminal_crow_worktree(
    conn: sqlite3.Connection,
    repo_root: Path,
    ticket_id: str,
) -> bool:
    """Prune a finished crow's worktree using the path stored at spawn time.

    The path is the durable source of truth (``agents.worktree_path``); if no
    crow ever recorded one, there is nothing to prune.
    """

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
    return False


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
    "ensure_worktree",
    "ensure_worktree_for_branch",
    "list_git_worktrees",
    "list_git_worktrees_sync",
    "list_murder_worktrees_sync",
    "prune_terminal_crow_worktree",
    "prune_worktree_path",
    "safe_branch_name",
    "safe_branch_segment",
    "worktree_ref",
]
