from __future__ import annotations

import subprocess
from pathlib import Path

from murder.state.storage.git_transit import (
    build_transit_snapshot,
    transit_fingerprint,
)


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


def _commit(repo: Path, name: str, body: str = "") -> None:
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", name)
    message = name if not body else f"{name}\n\n{body}"
    _git(repo, "commit", "-m", message)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    return repo


def _make_repo_with_branch(tmp_path: Path) -> tuple[Path, str, Path]:
    """main with two commits + a worktree branch forked off main with a body."""
    repo = _init_repo(tmp_path)
    _commit(repo, "base")
    _commit(repo, "second")

    fork_point = _git_out(repo, "rev-parse", "HEAD")

    wt_path = repo / ".murder" / "worktrees" / "rogue" / "feature"
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", "feature/work", str(wt_path), "main")
    _commit(wt_path, "feature-a", body="line one\nline two")
    _commit(wt_path, "feature-b")

    return repo, fork_point, wt_path


def test_build_transit_snapshot_lanes_and_fork(tmp_path: Path) -> None:
    repo, fork_point, _wt = _make_repo_with_branch(tmp_path)

    snap = build_transit_snapshot(repo)

    branches = [lane.branch for lane in snap.lanes]
    assert "main" in branches
    assert "feature/work" in branches

    by_branch = {lane.branch: lane for lane in snap.lanes}

    main_lane = by_branch["main"]
    assert main_lane.is_main is True
    assert main_lane.fork_sha is None
    assert main_lane.worktree_path is None
    assert main_lane.head_sha == _git_out(repo, "rev-parse", "main")

    feature_lane = by_branch["feature/work"]
    assert feature_lane.is_main is False
    # fork_sha == merge-base main feature == the fork point on main.
    assert feature_lane.fork_sha == fork_point
    assert feature_lane.worktree_path is not None
    assert feature_lane.head_sha == _git_out(repo, "rev-parse", "feature/work")


def test_build_transit_snapshot_commit_details(tmp_path: Path) -> None:
    repo, fork_point, _wt = _make_repo_with_branch(tmp_path)

    snap = build_transit_snapshot(repo)
    feature_lane = {lane.branch: lane for lane in snap.lanes}["feature/work"]

    subjects = [c.subject for c in feature_lane.commits]
    # newest-first ordering.
    assert subjects[0] == "feature-b"
    assert subjects[1] == "feature-a"

    # Pre-fork shared ancestry is INCLUDED (not main..branch) so the client can
    # walk back across the fork into main.
    assert "second" in subjects
    assert "base" in subjects

    feature_a = next(c for c in feature_lane.commits if c.subject == "feature-a")
    assert feature_a.body.strip() == "line one\nline two"
    assert isinstance(feature_a.ts_epoch, int)
    assert feature_a.ts_epoch > 0
    assert len(feature_a.parents) == 1
    assert feature_a.parents[0] == fork_point
    assert feature_a.short and feature_a.sha.startswith(feature_a.short)

    assert snap.generated_at_epoch > 0
    assert snap.invalidation_key == transit_fingerprint(repo)


def test_transit_fingerprint_changes_after_commit(tmp_path: Path) -> None:
    repo, _fork, wt_path = _make_repo_with_branch(tmp_path)

    before = transit_fingerprint(repo)
    _commit(wt_path, "feature-c")
    after = transit_fingerprint(repo)

    assert before != after
