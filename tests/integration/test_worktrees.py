from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from murder.state.storage.worktrees import (
    WorktreeEntry,
    WorktreeError,
    WorktreeRef,
    ensure_worktree,
    ensure_worktree_for_branch,
    list_git_worktrees,
    prune_terminal_crow_worktree,
    prune_worktree_path,
    safe_branch_name,
    safe_branch_segment,
    worktree_ref,
)


def test_safe_branch_name_allows_slashes() -> None:
    assert safe_branch_name("feature/my-work") == "feature/my-work"


def test_safe_branch_name_rejects_empty() -> None:
    with pytest.raises(ValueError, match="required"):
        safe_branch_name("   ")


def test_worktree_ref_is_flat(repo_root: Path) -> None:
    ref = worktree_ref(repo_root, "feature/experiment")

    assert ref.branch == "feature/experiment"
    assert ref.path == repo_root / ".murder" / "worktrees" / "feature-experiment"


def test_list_git_worktrees_parses_porcelain(repo_root: Path, monkeypatch) -> None:
    porcelain = "\n".join(
        [
            f"worktree {repo_root}",
            "HEAD abc123",
            "branch refs/heads/main",
            "",
            f"worktree {repo_root / '.murder' / 'worktrees' / 'feat'}",
            "HEAD def456",
            "branch refs/heads/feature/feat",
            "",
        ]
    )

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        assert args == ("worktree", "list", "--porcelain")
        return 0, porcelain, ""

    monkeypatch.setattr("murder.state.storage.worktrees._git", fake_git)

    entries = asyncio.run(list_git_worktrees(repo_root))

    assert entries == [
        WorktreeEntry(path=repo_root, branch="main", is_main=True),
        WorktreeEntry(
            path=repo_root / ".murder" / "worktrees" / "feat",
            branch="feature/feat",
            is_main=False,
        ),
    ]


def test_ensure_worktree_for_branch_creates_real_git_worktree(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)

    ref = asyncio.run(ensure_worktree_for_branch(repo, "feature/rogue-test"))

    assert ref.path == repo / ".murder" / "worktrees" / "feature-rogue-test"
    assert (ref.path / ".git").exists()
    assert _git_out(ref.path, "branch", "--show-current") == "feature/rogue-test"


def test_ensure_worktree_for_branch_reuses_same_branch_silently(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)

    first = asyncio.run(ensure_worktree_for_branch(repo, "feature/dup"))
    second = asyncio.run(ensure_worktree_for_branch(repo, "feature/dup"))

    assert first.path == second.path
    assert _git_out(second.path, "branch", "--show-current") == "feature/dup"


def test_ensure_worktree_rejects_wrong_branch_at_path(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)

    # Provision a worktree, then ask for a DIFFERENT branch at the same path.
    asyncio.run(ensure_worktree_for_branch(repo, "feature/first"))
    occupied_path = repo / ".murder" / "worktrees" / "feature-first"
    colliding = WorktreeRef(branch="feature/second", path=occupied_path)

    with pytest.raises(WorktreeError, match="not 'feature/second'"):
        asyncio.run(ensure_worktree(repo, colliding))


def test_worktree_error_is_non_retryable() -> None:
    assert WorktreeError("boom").retryable is False


def test_safe_branch_segment_rejects_path_shape() -> None:
    assert safe_branch_segment("../bad ticket") == "bad-ticket"
    assert safe_branch_segment("///") == "agent"
    assert safe_branch_segment("bad..ref.lock") == "bad.ref-lock"


def test_prune_worktree_path_uses_git_safe_remove(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path)

    ref = asyncio.run(ensure_worktree_for_branch(repo, "feature/prune-me"))
    assert ref.path.exists()

    assert asyncio.run(prune_worktree_path(repo, ref.path)) is True
    assert not ref.path.exists()


def test_prune_terminal_crow_worktree_uses_stored_path(repo_root: Path, monkeypatch) -> None:
    from murder.state.persistence.agents import upsert_agent
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, status, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 'done', '2026-01-01', '2026-01-01')
        """
    )
    stored = str(repo_root / ".murder" / "worktrees" / "feature-stored")
    upsert_agent(
        conn,
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        session="murder_repo_crow_t001",
        harness="codex",
        model=None,
        status="done",
        start_commit=None,
        worktree_path=stored,
        pid=None,
    )

    pruned: list[str] = []

    async def fake_prune(_repo: Path, path: str | Path) -> bool:
        pruned.append(str(path))
        return True

    monkeypatch.setattr("murder.state.storage.worktrees.prune_worktree_path", fake_prune)

    assert asyncio.run(prune_terminal_crow_worktree(conn, repo_root, "t001")) is True
    assert pruned == [stored]


def test_prune_terminal_crow_worktree_noops_without_stored_path(repo_root: Path) -> None:
    from murder.state.persistence.schema import get_db, init_db

    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)

    assert asyncio.run(prune_terminal_crow_worktree(conn, repo_root, "missing")) is False


def test_prune_worktree_path_resolves_relative_paths_from_repo_root(
    repo_root: Path, monkeypatch
) -> None:
    worktree = repo_root / ".murder" / "worktrees" / "t001"
    worktree.mkdir(parents=True)
    calls: list[tuple[str, ...]] = []

    async def fake_git(_repo_root: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr("murder.state.storage.worktrees._git", fake_git)

    assert asyncio.run(prune_worktree_path(repo_root, ".murder/worktrees/t001")) is True
    assert calls == [("worktree", "remove", str(worktree))]


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    return repo


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
