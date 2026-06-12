"""Incremental update over a tiny temp git repo (t061).

A stub client serves file summaries and roll-ups; it records the system prompts
so the test can assert which files were re-summarized (call counts) and which
dirs were re-rolled. The DB is the canonical history — unchanged siblings are
read back from it at base_sha.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from murder.codebase_map.build import fresh_build, incremental_update
from murder.codebase_map.store import latest_map_sha, load_summary, rows_for_commit
from murder.codebase_map.summarize import FileSummarizer
from murder.llm.clients.base import CompletionResult
from murder.state.persistence.schema import SCHEMA_SQL
from murder.verdict.enforcement.git_diff import head_commit

_FILE_PATH_RE = re.compile(r"^File path: (.+)$", re.MULTILINE)
_DIR_RE = re.compile(r"^Directory: (.+)$", re.MULTILINE)


class RecordingStubClient:
    """Replies to every completion; records what each call summarized."""

    def __init__(self) -> None:
        self.file_paths: list[str] = []
        self.dir_paths: list[str] = []

    async def complete(self, **kwargs) -> CompletionResult:
        system = kwargs.get("system") or ""
        file_match = _FILE_PATH_RE.search(system)
        dir_match = _DIR_RE.search(system)
        if file_match:
            path = file_match.group(1).strip()
            self.file_paths.append(path)
            text = f"# summary of {path}"
        elif dir_match:
            path = dir_match.group(1).strip()
            self.dir_paths.append(path)
            text = f"# rollup of {path}"
        else:
            text = "# summary"
        return CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=10,
            completion_tokens=5,
            model="stub",
            latency_ms=1.0,
        )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True)


def _init_repo(root: Path) -> None:
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def a():\n    return 1\n")
    (root / "pkg" / "b.py").write_text("def b():\n    return 2\n")
    (root / "pkg" / "sub" / "c.py").write_text("def c():\n    return 3\n")
    (root / "top.py").write_text("def t():\n    return 0\n")
    # The map is gitignored in reality; mirror that so the rendered .murder/map
    # tree never shows up in the commit diff the updater inspects.
    (root / ".gitignore").write_text(".murder/\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA_SQL)
    return db


def _seed(root: Path, db: sqlite3.Connection) -> str:
    """Fresh-build at HEAD, return the base sha."""
    asyncio.run(fresh_build(root, FileSummarizer(RecordingStubClient()), db=db, concurrency=2))
    return latest_map_sha(db)


def test_incremental_resummarizes_only_changed_files():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)

        (root / "pkg" / "a.py").write_text("def a():\n    return 99\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "edit a")
        head = asyncio.run(head_commit(root))

        client = RecordingStubClient()
        asyncio.run(
            incremental_update(
                root, FileSummarizer(client), db=db, base_sha=base, head_sha=head, concurrency=2
            )
        )

        # Only the changed file was re-summarized.
        assert client.file_paths == ["pkg/a.py"]
        # Ancestor chain re-rolled: pkg/ and ROOT. pkg/sub/ is untouched.
        assert "pkg" in client.dir_paths
        assert "pkg/sub" not in client.dir_paths
        assert "." in client.dir_paths  # root_summary uses dir_path="."


def test_incremental_snapshots_rows_under_head_sha():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)

        (root / "pkg" / "a.py").write_text("def a():\n    return 7\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "edit")
        head = asyncio.run(head_commit(root))

        asyncio.run(
            incremental_update(
                root, FileSummarizer(RecordingStubClient()),
                db=db, base_sha=base, head_sha=head, concurrency=2,
            )
        )

        rows = {r["path"]: r for r in rows_for_commit(db, head)}
        # Changed file + re-rolled ancestor dirs + ROOT land under head_sha.
        assert "pkg/a.py" in rows
        assert "pkg" in rows
        assert "ROOT" in rows
        # Untouched leaf file/dir are NOT snapshotted under head (cheap update).
        assert "pkg/sub/c.py" not in rows
        assert "pkg/sub" not in rows


def test_incremental_reuses_unchanged_sibling_from_db():
    """Re-rolling pkg/ must re-feed pkg/b.py's body from the DB at base_sha,
    NOT re-summarize it."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)
        sibling_body = load_summary(db, "pkg/b.py", base)["body"]

        (root / "pkg" / "a.py").write_text("def a():\n    return 1234\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "edit a")
        head = asyncio.run(head_commit(root))

        client = RecordingStubClient()
        asyncio.run(
            incremental_update(
                root, FileSummarizer(client), db=db, base_sha=base, head_sha=head, concurrency=2
            )
        )
        # b.py was reused, not re-summarized.
        assert "pkg/b.py" not in client.file_paths
        # The reused body matches what the DB held at base.
        assert sibling_body == load_summary(db, "pkg/b.py", base)["body"]


def test_incremental_deletion_removes_rendered_file():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)
        rendered = root / ".murder" / "map" / "pkg" / "b.py.md"
        assert rendered.exists()

        (root / "pkg" / "b.py").unlink()
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "rm b")
        head = asyncio.run(head_commit(root))

        client = RecordingStubClient()
        asyncio.run(
            incremental_update(
                root, FileSummarizer(client), db=db, base_sha=base, head_sha=head, concurrency=2
            )
        )

        assert not rendered.exists()
        # Deleted file is not re-summarized, but pkg/ is still re-rolled.
        assert "pkg/b.py" not in client.file_paths
        assert "pkg" in client.dir_paths


def test_incremental_noop_when_no_changes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)

        client = RecordingStubClient()
        asyncio.run(
            incremental_update(
                root, FileSummarizer(client), db=db, base_sha=base, head_sha=base, concurrency=2
            )
        )
        assert client.file_paths == []
        assert client.dir_paths == []


def test_incremental_root_level_file_change_rerolls_root_only():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        base = _seed(root, db)

        (root / "top.py").write_text("def t():\n    return 42\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "edit top")
        head = asyncio.run(head_commit(root))

        client = RecordingStubClient()
        asyncio.run(
            incremental_update(
                root, FileSummarizer(client), db=db, base_sha=base, head_sha=head, concurrency=2
            )
        )
        assert client.file_paths == ["top.py"]
        # No DIR.md re-rolls (the only ancestor of a root-level file is ROOT).
        assert client.dir_paths == ["."]
        rows = {r["path"] for r in rows_for_commit(db, head)}
        assert {"top.py", "ROOT"} <= rows
        assert "pkg" not in rows
