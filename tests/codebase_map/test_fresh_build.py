"""End-to-end fresh_build over a tiny temp git repo (t059).

No network: a stub client serves both file summaries and roll-ups, and
records the max number of concurrent in-flight calls so the test can assert
the semaphore bound is respected.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from murder.codebase_map.build import fresh_build
from murder.codebase_map.summarize import FileSummarizer
from murder.llm.clients.base import CompletionResult


class ConcurrencyStubClient:
    """Replies to every completion; tracks max concurrent in-flight calls."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # Yield control so other coroutines can pile up if unbounded.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            self.in_flight -= 1
        return CompletionResult(
            text="# summary",
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=3,
            model="stub",
            latency_ms=1.0,
        )


def _init_repo(root: Path) -> None:
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def a():\n    return 1\n")
    (root / "pkg" / "b.py").write_text("def b():\n    return 2\n")
    (root / "pkg" / "sub" / "c.py").write_text("def c():\n    return 3\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_fresh_build_mirrors_tree_and_rolls_up():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        client = ConcurrencyStubClient()
        summarizer = FileSummarizer(client)

        asyncio.run(fresh_build(root, summarizer, concurrency=2))

        map_root = root / ".murder" / "map"
        # Tree mirrors the source, keeping source extensions.
        assert (map_root / "pkg" / "a.py.md").exists()
        assert (map_root / "pkg" / "b.py.md").exists()
        assert (map_root / "pkg" / "sub" / "c.py.md").exists()
        # DIR.md at each directory level + ROOT.md at top.
        assert (map_root / "pkg" / "DIR.md").exists()
        assert (map_root / "pkg" / "sub" / "DIR.md").exists()
        assert (map_root / "ROOT.md").exists()
        # Frontmatter carries source_hash.
        assert "source_hash:" in (map_root / "pkg" / "a.py.md").read_text()


def test_fresh_build_respects_concurrency_bound():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        client = ConcurrencyStubClient()
        summarizer = FileSummarizer(client)

        asyncio.run(fresh_build(root, summarizer, concurrency=2))
        assert client.max_in_flight <= 2


def test_fresh_build_snapshots_to_db():
    import sqlite3

    from murder.codebase_map.store import latest_map_sha, rows_for_commit
    from murder.state.persistence.schema import SCHEMA_SQL

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA_SQL)

        client = ConcurrencyStubClient()
        summarizer = FileSummarizer(client)
        asyncio.run(fresh_build(root, summarizer, db=db, concurrency=2))

        sha = latest_map_sha(db)
        assert sha is not None
        rows = rows_for_commit(db, sha)
        paths = {r["path"] for r in rows}
        kinds = {r["kind"] for r in rows}
        # Every file + dir rollups + root snapshotted.
        assert {"pkg/a.py", "pkg/b.py", "pkg/sub/c.py", "ROOT"} <= paths
        assert {"file", "dir", "root"} <= kinds


def test_fresh_build_blows_away_stale_tree():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        map_root = root / ".murder" / "map"
        map_root.mkdir(parents=True)
        stale = map_root / "stale.md"
        stale.write_text("old")

        client = ConcurrencyStubClient()
        summarizer = FileSummarizer(client)
        asyncio.run(fresh_build(root, summarizer, concurrency=2))

        assert not stale.exists()
