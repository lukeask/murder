"""Git commit-graph read-model for the Transit right-rail panel.

Derives a navigable per-lane commit graph from git on demand: ``main`` (the
trunk) plus every branch checked out under ``.murder/worktrees``. Nothing is
persisted — the graph is rebuilt from git when a client refetches, and a cheap
fingerprint (``transit_fingerprint``) lets the service poll loop detect HEAD
changes without doing the full ``git log`` work.

Git calls are synchronous ``subprocess`` (mirroring ``list_git_worktrees_sync``
in ``worktrees.py``); ``build_transit_snapshot`` runs inside the read-model on
demand, not on the hot poll path.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from murder.state.storage.worktrees import (
    list_git_worktrees_sync,
    list_murder_worktrees_sync,
)

# Bound the per-lane log by both count and time window so duration-jumps
# (``g20d`` / ``g30d``) resolve while keeping cost predictable.
TRANSIT_MAX_COMMITS = 80
TRANSIT_SINCE = "35 days ago"

# git log record/field separators: records by 0x1e (RS), fields by NUL.
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x00"
_LOG_FORMAT = "%H%x00%h%x00%ct%x00%P%x00%s%x00%b%x1e"
# Field count emitted by _LOG_FORMAT: sha, short, ct, parents, subject, body.
_LOG_FIELD_COUNT = 6


@dataclass(frozen=True, slots=True)
class TransitCommit:
    sha: str
    short: str
    subject: str
    body: str
    ts_epoch: int
    parents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransitLane:
    branch: str
    is_main: bool
    worktree_path: str | None
    head_sha: str
    fork_sha: str | None
    commits: tuple[TransitCommit, ...]


@dataclass(frozen=True, slots=True)
class TransitSnapshot:
    lanes: tuple[TransitLane, ...]
    generated_at_epoch: int
    invalidation_key: str


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return int(result.returncode), result.stdout


def _resolve_main_branch(repo_root: Path, worktrees) -> str:
    """Resolve the trunk branch name robustly.

    Prefer the literal ``main``; otherwise fall back to the branch of the
    worktree git marks as the main checkout (``is_main``).
    """
    rc, _out = _git(repo_root, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    if rc == 0:
        return "main"
    for entry in worktrees:
        if entry.is_main and entry.branch:
            return entry.branch
    # Last resort: list_murder_worktrees_sync excludes the main checkout, so
    # query the full worktree set for the main branch name.
    for entry in list_git_worktrees_sync(repo_root):
        if entry.is_main and entry.branch:
            return entry.branch
    return "main"


def _parse_log(text: str) -> tuple[TransitCommit, ...]:
    commits: list[TransitCommit] = []
    for raw_record in text.split(_RECORD_SEP):
        record = raw_record.strip("\n")
        if not record.strip():
            continue
        fields = record.split(_FIELD_SEP)
        if len(fields) < _LOG_FIELD_COUNT:
            continue
        sha, short, ct, parents_raw, subject, body = fields[:_LOG_FIELD_COUNT]
        try:
            ts_epoch = int(ct)
        except ValueError:
            continue
        parents = tuple(p for p in parents_raw.split() if p)
        commits.append(
            TransitCommit(
                sha=sha,
                short=short,
                subject=subject,
                body=body,
                ts_epoch=ts_epoch,
                parents=parents,
            )
        )
    return tuple(commits)


def _lane_commits(repo_root: Path, branch: str) -> tuple[TransitCommit, ...]:
    # Qualify the ref as refs/heads/<branch> so a branch name like ``--all`` or
    # ``-n5`` can't be parsed as a git flag, and append ``--`` so it can't be
    # taken as a pathspec.
    rc, out = _git(
        repo_root,
        "log",
        f"--max-count={TRANSIT_MAX_COMMITS}",
        f"--since={TRANSIT_SINCE}",
        f"--format={_LOG_FORMAT}",
        f"refs/heads/{branch}",
        "--",
    )
    if rc != 0:
        return ()
    return _parse_log(out)


def _lane_set(repo_root: Path) -> tuple[str, list[tuple[str, str | None]]]:
    """Return (main_branch, [(branch, worktree_path)]) de-duped, main first."""
    worktrees = list_murder_worktrees_sync(repo_root)
    main_branch = _resolve_main_branch(repo_root, worktrees)

    ordered: list[tuple[str, str | None]] = [(main_branch, None)]
    seen = {main_branch}
    for entry in worktrees:
        if not entry.branch or entry.branch in seen:
            continue
        seen.add(entry.branch)
        ordered.append((entry.branch, str(entry.path)))
    return main_branch, ordered


def build_transit_snapshot(repo_root: Path) -> TransitSnapshot:
    repo_root = Path(repo_root)
    main_branch, lane_specs = _lane_set(repo_root)

    lanes: list[TransitLane] = []
    for branch, worktree_path in lane_specs:
        is_main = branch == main_branch
        # Qualify as refs/heads/<branch> so the rev never resolves to a flag or
        # pathspec; an unborn/detached HEAD or a non-existent main fails here.
        rc, head_out = _git(repo_root, "rev-parse", "--verify", f"refs/heads/{branch}")
        head_sha = head_out.strip() if rc == 0 else ""
        if not head_sha:
            # No resolvable branch head (empty repo, detached HEAD, or a main
            # that doesn't exist as a branch): skip the lane rather than emit one
            # with an empty sha the rail can't render.
            continue

        fork_sha: str | None = None
        if not is_main:
            rc, mb_out = _git(
                repo_root,
                "merge-base",
                f"refs/heads/{main_branch}",
                f"refs/heads/{branch}",
            )
            if rc == 0 and mb_out.strip():
                fork_sha = mb_out.strip()

        lanes.append(
            TransitLane(
                branch=branch,
                is_main=is_main,
                worktree_path=worktree_path,
                head_sha=head_sha,
                fork_sha=fork_sha,
                commits=_lane_commits(repo_root, branch),
            )
        )

    return TransitSnapshot(
        lanes=tuple(lanes),
        generated_at_epoch=int(time.time()),
        invalidation_key=transit_fingerprint(repo_root),
    )


def transit_fingerprint(repo_root: Path) -> str:
    """Cheap change-detection key: HEAD shas of main + worktree branches.

    Used by the service poll loop to detect branch movement without doing the
    full ``git log`` work in ``build_transit_snapshot``.
    """
    repo_root = Path(repo_root)
    main_branch, lane_specs = _lane_set(repo_root)
    refs = {f"refs/heads/{branch}" for branch, _ in lane_specs}

    rc, out = _git(
        repo_root,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        *sorted(refs),
    )
    if rc != 0:
        return ""
    lines = sorted(line.strip() for line in out.splitlines() if line.strip())
    return "\n".join(lines)


__all__ = [
    "TRANSIT_MAX_COMMITS",
    "TRANSIT_SINCE",
    "TransitCommit",
    "TransitLane",
    "TransitSnapshot",
    "build_transit_snapshot",
    "transit_fingerprint",
]
