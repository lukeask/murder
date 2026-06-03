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


