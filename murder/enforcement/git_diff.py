"""Post-hoc git diff write-set check (D5 layer 2)."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterable
from pathlib import Path


async def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    def _run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    proc = await asyncio.to_thread(_run)
    return proc.returncode, proc.stdout, proc.stderr


async def head_commit(repo_root: Path) -> str:
    rc, out, err = await _git("rev-parse", "HEAD", cwd=repo_root)
    if rc != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {err.strip()}")
    return out.strip()


async def diff_files(repo_root: Path, since_commit: str) -> list[Path]:
    """Return paths changed since `since_commit` (paths relative to repo_root)."""
    rc, out, err = await _git(
        "diff", "--name-only", since_commit, "HEAD", cwd=repo_root
    )
    if rc != 0:
        raise RuntimeError(f"git diff failed: {err.strip()}")
    return [Path(p) for p in out.splitlines() if p.strip()]


async def diff_outside(
    repo_root: Path,
    since_commit: str,
    write_set: Iterable[Path],
) -> list[Path]:
    """Files changed since `since_commit` that are NOT in `write_set`."""
    changed = await diff_files(repo_root, since_commit)
    allowed = {Path(p) for p in write_set}
    return [c for c in changed if c not in allowed]
