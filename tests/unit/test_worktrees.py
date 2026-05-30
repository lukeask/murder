from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from murder.storage.worktrees import (
    WorktreeEntry,
    crow_worktree_ref,
    ensure_crow_worktree,
    ensure_named_worktree,
    list_git_worktrees,
    named_worktree_ref,
    prune_crow_worktree,
    prune_worktree_path,
    rogue_worktree_ref,
    safe_branch_name,
    safe_branch_segment,
)


def test_safe_branch_name_allows_slashes() -> None:
    assert safe_branch_name("feature/my-work") == "feature/my-work"


def test_safe_branch_name_rejects_empty() -> None:
    import pytest

    with pytest.raises(ValueError, match="required"):
        safe_branch_name("   ")


def test_rogue_worktree_ref_uses_branch_name(repo_root: Path) -> None:
    ref = rogue_worktree_ref(repo_root, "feature/experiment")

    assert ref.branch == "feature/experiment"
    assert ref.path == repo_root / ".murder" / "worktrees" / "rogue" / "feature-experiment"


def test_named_worktree_ref_custom_category(repo_root: Path) -> None:
    ref = named_worktree_ref(repo_root, "murder/crow/t001", category="crow")

    assert ref.branch == "murder/crow/t001"
    assert ref.path == repo_root / ".murder" / "worktrees" / "crow" / "murder-crow-t001"


def test_list_git_worktrees_parses_porcelain(repo_root: Path, monkeypatch) -> None:
    porcelain = "\n".join(
        [
            f"worktree {repo_root}",
            "HEAD abc123",
            "branch refs/heads/main",
            "",
            f"worktree {repo_root / '.murder' / 'worktrees' / 'rogue' / 'feat'}",
            "HEAD def456",
            "branch refs/heads/feature/feat",
            "",
        ]
    )

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        assert args == ("worktree", "list", "--porcelain")
        return 0, porcelain, ""

    monkeypatch.setattr("murder.storage.worktrees._git", fake_git)

    entries = asyncio.run(list_git_worktrees(repo_root))

    assert entries == [
        WorktreeEntry(path=repo_root, branch="main", is_main=True),
        WorktreeEntry(
            path=repo_root / ".murder" / "worktrees" / "rogue" / "feat",
            branch="feature/feat",
            is_main=False,
        ),
    ]


def test_ensure_named_worktree_creates_real_git_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")

    ref = asyncio.run(ensure_named_worktree(repo, "feature/rogue-test", category="rogue"))

    assert (ref.path / ".git").exists()
    assert _git_out(ref.path, "branch", "--show-current") == "feature/rogue-test"


def test_crow_worktree_ref_uses_murder_worktrees(repo_root: Path) -> None:
    ref = crow_worktree_ref(repo_root, "t001")

    assert ref.branch == "murder/crow/t001"
    assert ref.path == repo_root / ".murder" / "worktrees" / "crow" / "t001"


def test_safe_branch_segment_rejects_path_shape() -> None:
    assert safe_branch_segment("../bad ticket") == "bad-ticket"
    assert safe_branch_segment("///") == "agent"
    assert safe_branch_segment("bad..ref.lock") == "bad.ref-lock"


def test_ensure_crow_worktree_creates_branch_from_head(
    repo_root: Path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr("murder.storage.worktrees._git", fake_git)

    ref = asyncio.run(ensure_crow_worktree(repo_root, "t001"))

    assert ref.path == repo_root / ".murder" / "worktrees" / "crow" / "t001"
    assert calls == [
        (
            "worktree",
            "add",
            "-b",
            "murder/crow/t001",
            str(ref.path),
            "HEAD",
        )
    ]


def test_ensure_crow_worktree_reuses_existing_branch(
    repo_root: Path, monkeypatch
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        if len(calls) == 1:
            return 128, "", "fatal: a branch named 'murder/crow/t001' already exists"
        return 0, "", ""

    monkeypatch.setattr("murder.storage.worktrees._git", fake_git)

    ref = asyncio.run(ensure_crow_worktree(repo_root, "t001"))

    assert calls[-1] == ("worktree", "add", str(ref.path), "murder/crow/t001")


def test_ensure_crow_worktree_creates_real_git_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")

    ref = asyncio.run(ensure_crow_worktree(repo, "t001"))

    assert (ref.path / ".git").exists()
    assert (ref.path / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    assert _git_out(ref.path, "branch", "--show-current") == "murder/crow/t001"


def test_prune_crow_worktree_uses_git_safe_remove(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")

    ref = asyncio.run(ensure_crow_worktree(repo, "t001"))
    assert ref.path.exists()

    assert asyncio.run(prune_crow_worktree(repo, "t001")) is True
    assert not ref.path.exists()


def test_prune_worktree_path_resolves_relative_paths_from_repo_root(
    repo_root: Path, monkeypatch
) -> None:
    worktree = repo_root / ".murder" / "worktrees" / "crow" / "t001"
    worktree.mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr("murder.storage.worktrees._git", fake_git)

    assert asyncio.run(prune_worktree_path(repo_root, ".murder/worktrees/crow/t001")) is True
    assert calls == [("worktree", "remove", str(worktree))]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
