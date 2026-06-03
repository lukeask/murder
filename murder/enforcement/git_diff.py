"""Post-hoc git diff write-set check (D5 layer 2)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from pathlib import Path


async def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
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


async def head_commit(repo_root: Path) -> str:
    rc, out, err = await _git("rev-parse", "HEAD", cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {err.strip()}")
    return out.strip()


async def diff_files(repo_root: Path, since_commit: str) -> list[Path]:
    """Return paths changed since `since_commit` (paths relative to repo_root)."""
    # Compares working tree against since_commit — intentional, because crows do NOT
    # commit; their changes live in the working tree. False-positive risk: pre-existing
    # dirty user files also show up. Real fix requires crow worktree isolation
    # (.murder/worktrees/..., not yet implemented). Until then the policy layer must
    # NOT auto-revert on violations — see resolution_policy() in policy.py. (2026-06-02)
    rc, out, err = await _git("diff", "--name-only", since_commit, cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git diff failed: {err.strip()}")
    changed = [Path(p) for p in out.splitlines() if p.strip()]

    rc, out, err = await _git("ls-files", "--others", "--exclude-standard", cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git ls-files failed: {err.strip()}")
    changed.extend(Path(p) for p in out.splitlines() if p.strip())

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in changed:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _is_allowed(path: Path, allowed: set[Path]) -> bool:
    return any(path == root or root in path.parents for root in allowed)


async def diff_outside(
    repo_root: Path,
    since_commit: str,
    write_set: Iterable[Path],
) -> list[Path]:
    """Files changed since `since_commit` that are NOT in `write_set`."""
    changed = await diff_files(repo_root, since_commit)
    allowed = {Path(p) for p in write_set}
    return [c for c in changed if not _is_allowed(c, allowed)]
