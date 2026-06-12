"""Git utilities for crow start-commit tracking."""

from __future__ import annotations

import asyncio
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


async def changed_files(
    repo_root: Path,
    base: str,
    head: str,
    *,
    cwd: Path | None = None,
) -> list[str]:
    """Return the repo-relative paths changed between ``base`` and ``head``.

    Mirrors :func:`head_commit`'s async ``create_subprocess_exec`` style:
    ``git -C <cwd or repo_root> diff --name-only <base> <head>``. The ``cwd``
    param exists so this doubles as the phase-2 agent touch-set helper (diff a
    worktree's ``start_commit``..HEAD inside the worktree).
    """
    run_cwd = cwd if cwd is not None else repo_root
    rc, out, err = await _git("diff", "--name-only", base, head, cwd=run_cwd)
    if rc != 0:
        raise RuntimeError(f"git diff --name-only failed: {err.strip()}")
    return [line for line in out.splitlines() if line]


