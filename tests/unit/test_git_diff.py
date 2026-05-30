from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from murder.enforcement.git_diff import diff_outside, head_commit


def test_diff_outside_includes_working_tree_and_untracked_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "src").mkdir()
    (repo / "src" / "base.txt").write_text("base\n", encoding="utf-8")
    (repo / "outside.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "src/base.txt", "outside.txt")
    _git(repo, "commit", "-m", "base")
    start_commit = asyncio.run(head_commit(repo))

    (repo / "src" / "base.txt").write_text("allowed tracked\n", encoding="utf-8")
    (repo / "src" / "new.txt").write_text("allowed untracked\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside tracked\n", encoding="utf-8")
    (repo / "other.txt").write_text("outside untracked\n", encoding="utf-8")

    assert asyncio.run(diff_outside(repo, start_commit, [Path("src")])) == [
        Path("outside.txt"),
        Path("other.txt"),
    ]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
