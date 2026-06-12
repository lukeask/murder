"""Real-git tests for changed_files (t061).

changed_files mirrors head_commit's async create_subprocess_exec style and
doubles as the phase-2 touch-set helper via the cwd param.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from murder.verdict.enforcement.git_diff import changed_files, head_commit


def _run(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True)


def _init_repo(root: Path) -> str:
    _run(root, "init", "-q")
    _run(root, "config", "user.email", "t@t")
    _run(root, "config", "user.name", "t")
    (root / "a.py").write_text("a = 1\n")
    (root / "b.py").write_text("b = 1\n")
    _run(root, "add", "-A")
    _run(root, "commit", "-q", "-m", "init")
    return asyncio.run(head_commit(root))


def test_changed_files_lists_modified_and_added():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        base = _init_repo(root)
        (root / "a.py").write_text("a = 2\n")  # modified
        (root / "c.py").write_text("c = 1\n")  # added
        _run(root, "add", "-A")
        _run(root, "commit", "-q", "-m", "change")
        head = asyncio.run(head_commit(root))

        changed = asyncio.run(changed_files(root, base, head))
        assert set(changed) == {"a.py", "c.py"}


def test_changed_files_lists_deletions():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        base = _init_repo(root)
        (root / "b.py").unlink()
        _run(root, "add", "-A")
        _run(root, "commit", "-q", "-m", "del")
        head = asyncio.run(head_commit(root))

        changed = asyncio.run(changed_files(root, base, head))
        assert changed == ["b.py"]


def test_changed_files_empty_when_no_diff():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        base = _init_repo(root)
        assert asyncio.run(changed_files(root, base, base)) == []


def test_changed_files_cwd_override_for_touch_sets():
    """The cwd param runs the diff in a different dir (phase-2 touch-set)."""
    with tempfile.TemporaryDirectory() as d:
        worktree = Path(d) / "wt"
        worktree.mkdir()
        base = _init_repo(worktree)
        (worktree / "a.py").write_text("a = 9\n")
        _run(worktree, "add", "-A")
        _run(worktree, "commit", "-q", "-m", "x")
        head = asyncio.run(head_commit(worktree))

        # repo_root points elsewhere; cwd steers the actual git invocation.
        changed = asyncio.run(
            changed_files(Path(d), base, head, cwd=worktree)
        )
        assert changed == ["a.py"]
