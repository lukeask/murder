"""``reconcile_map`` — the resumable, per-file-content-hash map engine the
background worker drives every tick.

The properties that matter (and were the whole point of the rewrite):

- a first run over an empty DB summarizes every file and persists as it goes;
- a second run with nothing changed makes ZERO model calls (no re-burning the
  API on every launch);
- an interrupted build resumes — only the files missing a current-hash
  snapshot are summarized, the rest are reused;
- a file whose ``<file>.md`` went missing but whose snapshot is current is
  re-rendered without a model call;
- editing a file (no git commit needed) re-summarizes only that file;
- deleting a file prunes its rendered node without summarizing anything.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from murder.codebase_map.build import reconcile_map
from murder.codebase_map.store import load_latest_summary
from murder.codebase_map.summarize import FileSummarizer
from murder.llm.clients.base import CompletionResult
from murder.state.persistence.schema import SCHEMA_SQL

_FILE_PATH_RE = re.compile(r"^File path: (.+)$", re.MULTILINE)
_DIR_RE = re.compile(r"^Directory: (.+)$", re.MULTILINE)


class RecordingStubClient:
    """Replies to every completion; records what each call summarized."""

    def __init__(self) -> None:
        self.file_paths: list[str] = []
        self.dir_paths: list[str] = []
        self.systems: list[str] = []

    async def complete(self, **kwargs) -> CompletionResult:
        system = kwargs.get("system") or ""
        self.systems.append(system)
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


def _reconcile(root: Path, db: sqlite3.Connection) -> RecordingStubClient:
    client = RecordingStubClient()
    asyncio.run(reconcile_map(root, FileSummarizer(client), db=db, concurrency=2))
    return client


def test_first_run_summarizes_and_renders_everything():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()

        client = _reconcile(root, db)

        assert {
            "pkg/a.py", "pkg/b.py", "pkg/sub/c.py", "top.py", ".gitignore",
        } == set(client.file_paths)
        map_root = root / ".murder" / "map"
        assert (map_root / "pkg" / "a.py.md").exists()
        assert (map_root / "pkg" / "sub" / "DIR.md").exists()
        assert (map_root / "ROOT.md").exists()
        assert load_latest_summary(db, "pkg/a.py") is not None


def test_second_run_is_a_no_op_zero_model_calls():
    """The core fix: once current, a reconcile burns no API."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)  # first full build

        client = _reconcile(root, db)  # nothing changed

        assert client.file_paths == []
        assert client.dir_paths == []


def test_resumes_only_missing_files_after_interruption():
    """Simulate a crash mid-build: drop two files' snapshots + rendered nodes.
    The next reconcile re-summarizes ONLY those, reusing the rest."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)

        # Wipe the persisted progress for two files (as if never reached).
        map_root = root / ".murder" / "map"
        for path in ("pkg/b.py", "pkg/sub/c.py"):
            db.execute("DELETE FROM map_summaries WHERE path = ?", (path,))
            (map_root / (path + ".md")).unlink()

        client = _reconcile(root, db)

        assert sorted(client.file_paths) == ["pkg/b.py", "pkg/sub/c.py"]
        # The untouched files are NOT re-summarized.
        assert "pkg/a.py" not in client.file_paths
        assert "top.py" not in client.file_paths


def test_missing_render_is_repaired_without_a_model_call():
    """A current snapshot whose <file>.md vanished is re-rendered from the DB,
    not re-summarized."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)

        rendered = root / ".murder" / "map" / "pkg" / "a.py.md"
        rendered.unlink()

        client = _reconcile(root, db)

        assert rendered.exists()  # repaired
        assert "pkg/a.py" not in client.file_paths  # no file model call
        # A pure render-only repair touches NO rollup: no dir roll and no ROOT
        # roll (ROOT rolls record as "." via the "Directory: ." prompt marker).
        assert client.dir_paths == []
        assert "." not in client.dir_paths


def test_edited_file_resummarized_without_git_commit():
    """reconcile keys on content hash, so an uncommitted edit is caught."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)

        (root / "pkg" / "a.py").write_text("def a():\n    return 999\n")

        client = _reconcile(root, db)

        assert client.file_paths == ["pkg/a.py"]
        # Ancestor chain re-rolled; untouched leaf left alone.
        assert "pkg" in client.dir_paths
        assert "pkg/sub" not in client.dir_paths


def test_oversize_generated_and_fixture_files_are_never_summarized():
    """The pre-summarize blockers: 250 KB hard cap, lockfiles, .jsonl streams,
    and anything under a fixtures/ tree are excluded from the map entirely."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)

        # A genuinely large source file (> 250 KB) — must be skipped.
        (root / "pkg" / "huge.py").write_text("x = 1  # pad\n" * 30000)
        # A lockfile that slips past the .lock blocklist as plain .json.
        (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n')
        # A line-delimited data stream.
        (root / "pkg" / "events.jsonl").write_text('{"a":1}\n{"a":2}\n')
        # Test-data under a fixtures/ tree (even small JSON).
        (root / "tests" / "fixtures").mkdir(parents=True)
        (root / "tests" / "fixtures" / "expected.json").write_text('{"ok": true}\n')
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "add noise")

        db = _db()
        client = _reconcile(root, db)

        for ruled_out in (
            "pkg/huge.py",
            "package-lock.json",
            "pkg/events.jsonl",
            "tests/fixtures/expected.json",
        ):
            assert ruled_out not in client.file_paths
        # Real source is still summarized.
        assert "pkg/a.py" in client.file_paths
        # ...and nothing ruled out is persisted or rendered.
        assert load_latest_summary(db, "package-lock.json") is None
        assert not (root / ".murder" / "map" / "pkg" / "huge.py.md").exists()


def test_deleted_file_is_pruned_not_summarized():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)
        rendered = root / ".murder" / "map" / "pkg" / "b.py.md"
        assert rendered.exists()

        (root / "pkg" / "b.py").unlink()

        client = _reconcile(root, db)

        assert not rendered.exists()
        assert "pkg/b.py" not in client.file_paths
        # Parent re-rolled without the vanished child.
        assert "pkg" in client.dir_paths

        # ...and the tick AFTER cleanup is fully idle — a deleted file must not
        # re-roll its parent dir forever (it lingers in map_summaries history).
        again = _reconcile(root, db)
        assert again.file_paths == []
        assert again.dir_paths == []


def test_deletion_with_already_missing_render_still_rerolls_then_idles():
    """BUG B: a source file deleted while its <file>.md was ALREADY missing
    must still be caught (parent re-rolled to drop the child), and the DB
    tombstone must stop it re-firing on the next tick."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        db = _db()
        _reconcile(root, db)
        rendered = root / ".murder" / "map" / "pkg" / "b.py.md"
        assert rendered.exists()

        # Simulate a render that was already gone when the source vanished:
        # delete BOTH the source file and its rendered node.
        (root / "pkg" / "b.py").unlink()
        rendered.unlink()

        client = _reconcile(root, db)

        # The deletion is caught despite no <file>.md on disk: parent re-rolled.
        assert not rendered.exists()
        assert "pkg/b.py" not in client.file_paths
        assert "pkg" in client.dir_paths

        # The next tick is fully idle — the DB tombstone (pruned file history)
        # stops the deletion re-firing every tick.
        again = _reconcile(root, db)
        assert again.file_paths == []
        assert again.dir_paths == []
