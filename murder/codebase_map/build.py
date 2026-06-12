"""Full fresh build of the codebase map (t059 + t060).

``fresh_build`` walks the repo's tracked text files, fans out per-file
summaries under a concurrency cap, rolls them up bottom-up into ``DIR.md`` /
``ROOT.md``, and renders the whole ``.murder/map/`` mirror tree. A fresh build
blows away and regenerates the tree.

When a ``db`` handle is supplied (t060) it also snapshots every file/dir/root
summary to the ``map_summaries`` table keyed by the current HEAD commit, so the
DB is the canonical history while disk is the live working copy.

Manual entrypoint:

    python -m murder.codebase_map.build [repo_root] [--no-db]
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
from pathlib import Path

from murder.codebase_map.render import (
    map_root_for,
    render_dir_summary,
    render_file_summary,
    render_root,
)
from murder.codebase_map.rollup import ChildEntry, dir_summary, root_summary
from murder.codebase_map.summarize import FileSummarizer, FileSummary
from murder.codebase_map.tokens import count_tokens

# Extensions we treat as binary/non-text and skip outright.
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z",
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib", ".dll", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".ogg", ".mov", ".avi", ".webm",
    ".db", ".sqlite", ".sqlite3", ".lock",
    ".jar", ".class", ".wasm", ".node",
}


async def _git_ls_files(repo_root: Path) -> list[str]:
    """Return the repo-relative paths of all tracked files (the map's source)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_root),
        "ls-files",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_raw, stderr_raw = await proc.communicate()
    if proc.returncode:
        err = stderr_raw.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed: {err}")
    out = stdout_raw.decode("utf-8", errors="replace")
    return [line for line in out.splitlines() if line]


def _is_text(repo_rel: str) -> bool:
    return Path(repo_rel).suffix.lower() not in _BINARY_EXTS


def _dir_of(repo_rel: str) -> str:
    parent = str(Path(repo_rel).parent)
    return "" if parent == "." else parent


async def fresh_build(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection | None = None,
    concurrency: int = 8,
) -> None:
    """Blow away and regenerate the whole ``.murder/map/`` tree.

    Bottom-up: file summaries → per-dir ``DIR.md`` (deepest first, from the
    child file summaries) → ``ROOT.md`` (from the dir bodies). When ``db`` is
    given, every summary is also snapshotted under the current HEAD commit
    (t060).
    """
    repo_root = Path(repo_root)
    map_root = map_root_for(repo_root)

    tracked = [p for p in await _git_ls_files(repo_root) if _is_text(p)]

    # Read each source up front (cheap, sequential, no event-loop contention).
    sources: dict[str, str] = {}
    for repo_rel in tracked:
        abs_path = repo_root / repo_rel
        try:
            sources[repo_rel] = abs_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Undeclared binary or unreadable file — skip it.
            continue

    paths = list(sources)

    # Fan out file summaries under a semaphore. Pre-warm with one summary
    # before gather() to dodge the ChatCompletionsClient lazy-init race.
    summaries: dict[str, FileSummary] = {}
    if paths:
        sem = asyncio.Semaphore(concurrency)

        async def _summarize(repo_rel: str) -> None:
            async with sem:
                summaries[repo_rel] = await summarizer.summarize(repo_rel, sources[repo_rel])

        first = paths[0]
        await _summarize(first)
        if len(paths) > 1:
            await asyncio.gather(*(_summarize(p) for p in paths[1:]))

    # Snapshot wiring (t060): resolve HEAD once, snapshot as we render.
    commit_sha: str | None = None
    store = None
    if db is not None:
        from murder.codebase_map import store as store_mod
        from murder.verdict.enforcement.git_diff import head_commit

        store = store_mod
        commit_sha = await head_commit(repo_root)

    # Regenerate the tree from scratch.
    if map_root.exists():
        shutil.rmtree(map_root)
    map_root.mkdir(parents=True, exist_ok=True)

    # Render every file summary; group paths by their containing directory.
    by_dir: dict[str, list[str]] = {}
    for repo_rel in paths:
        summary = summaries[repo_rel]
        render_file_summary(map_root, repo_rel, summary)
        if store is not None and commit_sha is not None:
            store.snapshot_file(db, repo_rel, commit_sha, summary)
        by_dir.setdefault(_dir_of(repo_rel), []).append(repo_rel)

    # Bottom-up dir roll-ups, deepest first. A directory's children are its
    # files plus the DIR summaries of its immediate subdirectories.
    dir_bodies: dict[str, str] = {}
    for dir_rel in sorted(by_dir, key=lambda d: d.count("/") if d else -1, reverse=True):
        children: list[ChildEntry] = []
        for repo_rel in sorted(by_dir[dir_rel]):
            children.append((Path(repo_rel).name, summaries[repo_rel].body))
        for sub_rel, sub_body in dir_bodies.items():
            if _dir_of(sub_rel) == dir_rel and sub_rel != dir_rel:
                children.append((Path(sub_rel).name + "/", sub_body))
        body = await dir_summary(summarizer.client, dir_rel or ".", children)
        dir_bodies[dir_rel] = body
        render_dir_summary(map_root, dir_rel, body)
        if store is not None and commit_sha is not None:
            store.snapshot_rollup(
                db, dir_rel or ".", commit_sha, "dir", body, summary_tokens=count_tokens(body)
            )

    # Root roll-up from the top-level directory bodies.
    root_children: list[ChildEntry] = []
    for dir_rel in sorted(dir_bodies):
        name = dir_rel if dir_rel else "."
        root_children.append((name, dir_bodies[dir_rel]))
    root_body = await root_summary(summarizer.client, root_children)
    render_root(map_root, root_body)
    if store is not None and commit_sha is not None:
        store.snapshot_rollup(
            db, "ROOT", commit_sha, "root", root_body, summary_tokens=count_tokens(root_body)
        )


async def _amain(repo_root: Path, *, use_db: bool) -> None:
    from murder.llm.clients.auto_free import AutoFreeClient

    client = AutoFreeClient.build_default()
    if client is None:
        raise SystemExit("no free LLM client available — set a provider key")
    summarizer = FileSummarizer(client)

    db: sqlite3.Connection | None = None
    if use_db:
        from murder.state.persistence.schema import get_db, init_db
        from murder.state.storage.paths import db_path

        db = get_db(db_path(repo_root))
        init_db(db)

    try:
        await fresh_build(repo_root, summarizer, db=db)
    finally:
        if db is not None:
            db.close()


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    # Default snapshots to the repo DB (t060); --no-db is render-only.
    use_db = True
    if "--no-db" in args:
        use_db = False
        args = [a for a in args if a != "--no-db"]
    repo_root = Path(args[0]).resolve() if args else Path.cwd()
    asyncio.run(_amain(repo_root, use_db=use_db))


if __name__ == "__main__":
    main()
