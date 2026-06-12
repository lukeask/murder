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
import contextlib
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


def _ancestor_dirs(repo_rel: str) -> list[str]:
    """Every dir on the path from ``repo_rel`` up to (not including) the root.

    Deepest first. ``"a/b/c.py"`` -> ``["a/b", "a"]``; root-level -> ``[]``.
    """
    out: list[str] = []
    d = _dir_of(repo_rel)
    while d:
        out.append(d)
        d = _dir_of(d)
    return out


def _dir_closure(paths: list[str]) -> set[str]:
    """All dirs on every path from each file up to (not including) the root.

    Shared by fresh and incremental builds so a dir holding only subdirectories
    still gets a ``DIR.md`` (the incremental update re-rolls the ancestor chain;
    each link must exist).
    """
    dirs: set[str] = set()
    for repo_rel in paths:
        dirs.update(_ancestor_dirs(repo_rel))
    return dirs


def _dir_children(
    dir_rel: str,
    *,
    by_dir: dict[str, list[str]],
    file_bodies: dict[str, str],
    dir_bodies: dict[str, str],
) -> list[ChildEntry]:
    """A directory's roll-up children: its files + its immediate subdir bodies.

    Files contribute ``(name, file-summary-body)``; immediate subdirectories
    contribute ``(name + "/", DIR.md-body)``. Both sorted for determinism.
    """
    children: list[ChildEntry] = []
    for repo_rel in sorted(by_dir.get(dir_rel, [])):
        children.append((Path(repo_rel).name, file_bodies[repo_rel]))
    for sub_rel in sorted(dir_bodies):
        if _dir_of(sub_rel) == dir_rel:
            children.append((Path(sub_rel).name + "/", dir_bodies[sub_rel]))
    return children


def _root_children(
    *,
    by_dir: dict[str, list[str]],
    file_bodies: dict[str, str],
    dir_bodies: dict[str, str],
) -> list[ChildEntry]:
    """ROOT's children: root-level files + TOP-LEVEL directory bodies only.

    Nested dirs are already compressed into their parents' ``DIR.md`` — feeding
    them to ROOT as well would double-count and break the pyramid.
    """
    children: list[ChildEntry] = []
    for repo_rel in sorted(by_dir.get("", [])):
        children.append((Path(repo_rel).name, file_bodies[repo_rel]))
    for dir_rel in sorted(dir_bodies):
        if "/" not in dir_rel:
            children.append((dir_rel + "/", dir_bodies[dir_rel]))
    return children


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
    file_bodies: dict[str, str] = {}
    for repo_rel in paths:
        summary = summaries[repo_rel]
        render_file_summary(map_root, repo_rel, summary)
        if store is not None and commit_sha is not None:
            store.snapshot_file(db, repo_rel, commit_sha, summary)
        file_bodies[repo_rel] = summary.body
        by_dir.setdefault(_dir_of(repo_rel), []).append(repo_rel)

    # Directory closure: every dir on the path from a file up to (but not
    # including) the repo root, so dirs holding only subdirectories still get
    # a DIR.md (t061 re-rolls the ancestor DIR.md chain — it must exist).
    dirs = _dir_closure(paths)

    # Bottom-up dir roll-ups, deepest first. A directory's children are its
    # files plus the DIR summaries of its immediate subdirectories.
    dir_bodies: dict[str, str] = {}
    for dir_rel in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
        children = _dir_children(
            dir_rel, by_dir=by_dir, file_bodies=file_bodies, dir_bodies=dir_bodies
        )
        body = await dir_summary(summarizer.client, dir_rel, children)
        dir_bodies[dir_rel] = body
        render_dir_summary(map_root, dir_rel, body)
        if store is not None and commit_sha is not None:
            store.snapshot_rollup(
                db, dir_rel, commit_sha, "dir", body, summary_tokens=count_tokens(body)
            )

    # Root roll-up: root-level files + TOP-LEVEL directory bodies only.
    root_children = _root_children(
        by_dir=by_dir, file_bodies=file_bodies, dir_bodies=dir_bodies
    )
    root_body = await root_summary(summarizer.client, root_children)
    render_root(map_root, root_body)
    if store is not None and commit_sha is not None:
        store.snapshot_rollup(
            db, "ROOT", commit_sha, "root", root_body, summary_tokens=count_tokens(root_body)
        )


async def incremental_update(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection,
    base_sha: str,
    head_sha: str,
    concurrency: int = 8,
) -> None:
    """Re-summarize only changed files and re-roll the ancestor DIR.md chains.

    Diff ``base_sha``..``head_sha``; for each changed *tracked text* file:
    re-summarize it (render ``<file>.md`` + snapshot at ``head_sha``); a changed
    file that was deleted has its rendered ``<file>.md`` removed. Then re-roll
    ONLY the ancestor ``DIR.md`` chain(s) of changed paths, up to ROOT, reusing
    the same closure / top-level-only rollup logic as :func:`fresh_build`.

    Unchanged sibling bodies needed to re-roll a parent are read back from the
    DB via the latest known row for that path (incremental rounds snapshot only
    changed paths under the new head, so the newest sibling row may sit several
    map shas back — an exact ``base_sha`` hit would miss it), falling back to a
    fresh roll when missing. A directory left with no tracked text files (last
    file deleted) has its ``DIR.md`` removed instead of being re-rolled empty.
    Empty summaries (``summary_tokens == 0``) are fine throughout.
    """
    repo_root = Path(repo_root)
    map_root = map_root_for(repo_root)

    from murder.codebase_map import store as store_mod
    from murder.verdict.enforcement.git_diff import changed_files

    changed = [p for p in await changed_files(repo_root, base_sha, head_sha) if _is_text(p)]
    if not changed:
        return

    # Which changed paths still exist as tracked text files at head (modified /
    # added) vs. were deleted. ls-files reflects the working tree, which the
    # worker drives at head — adequate for the poll-on-HEAD model.
    tracked_now = {p for p in await _git_ls_files(repo_root) if _is_text(p)}
    present = [p for p in changed if p in tracked_now]
    deleted = [p for p in changed if p not in tracked_now]

    # Re-summarize present changed files (fan-out, pre-warm to dodge the
    # ChatCompletionsClient lazy-init race — same as fresh_build).
    sources: dict[str, str] = {}
    for repo_rel in present:
        try:
            sources[repo_rel] = (repo_root / repo_rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Vanished or undeclared-binary between diff and read — treat as
            # deleted so its stale node is removed.
            deleted.append(repo_rel)
            continue

    to_summarize = list(sources)
    new_summaries: dict[str, FileSummary] = {}
    if to_summarize:
        sem = asyncio.Semaphore(concurrency)

        async def _summarize(repo_rel: str) -> None:
            async with sem:
                new_summaries[repo_rel] = await summarizer.summarize(
                    repo_rel, sources[repo_rel]
                )

        first = to_summarize[0]
        await _summarize(first)
        if len(to_summarize) > 1:
            await asyncio.gather(*(_summarize(p) for p in to_summarize[1:]))

    # Render + snapshot changed file rows at head; cache fresh bodies.
    fresh_file_bodies: dict[str, str] = {}
    for repo_rel, summary in new_summaries.items():
        render_file_summary(map_root, repo_rel, summary)
        store_mod.snapshot_file(db, repo_rel, head_sha, summary)
        fresh_file_bodies[repo_rel] = summary.body

    # Remove rendered nodes for deletions (the snapshot history at base_sha is
    # retained; only the live working copy drops the file).
    for repo_rel in deleted:
        target = map_root / (repo_rel + ".md")
        if target.exists():
            target.unlink()

    # Current tracked text files grouped by dir. The full dir closure (from
    # head's ls-files) tells us, for any re-rolled dir, its complete set of
    # immediate subdirectories — including unchanged ones we must re-feed.
    by_dir: dict[str, list[str]] = {}
    for repo_rel in sorted(tracked_now):
        by_dir.setdefault(_dir_of(repo_rel), []).append(repo_rel)
    all_dirs = _dir_closure(list(tracked_now))

    # Dirs to re-roll: the ancestor chain of every changed path (present +
    # deleted). A deletion still re-rolls its former parents — but a dir that
    # no longer holds ANY tracked text file (last file deleted) has vanished:
    # remove its DIR.md instead of re-rolling an empty node, deepest first.
    affected_dirs = _dir_closure(present + deleted)
    vanished_dirs = affected_dirs - all_dirs
    affected_dirs &= all_dirs
    for dir_rel in sorted(vanished_dirs, key=lambda d: d.count("/"), reverse=True):
        target = map_root / dir_rel / "DIR.md"
        if target.exists():
            target.unlink()
        with contextlib.suppress(OSError):
            (map_root / dir_rel).rmdir()

    # Body of a file child — fresh if re-summarized this run, else the latest
    # DB row (canonical), else a fresh roll from disk (DB is the source of
    # truth; the disk fallback only fires when a snapshot is missing).
    async def _file_body(repo_rel: str) -> str:
        if repo_rel in fresh_file_bodies:
            return fresh_file_bodies[repo_rel]
        row = store_mod.load_latest_summary(db, repo_rel)
        if row is not None:
            return row["body"] or ""
        try:
            src = (repo_root / repo_rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return ""
        summary = await summarizer.summarize(repo_rel, src)
        fresh_file_bodies[repo_rel] = summary.body
        return summary.body

    # Body of an unchanged subdir child — the latest DB DIR row (canonical).
    def _reused_dir_body(dir_rel: str) -> str:
        row = store_mod.load_latest_summary(db, dir_rel)
        return (row["body"] if row is not None else "") or ""

    async def _build_children(dir_rel: str, dir_bodies: dict[str, str]) -> list[ChildEntry]:
        """File + immediate-subdir children for ``dir_rel``.

        Subdir bodies: re-rolled this run -> the fresh body in ``dir_bodies``;
        otherwise the latest DB row (canonical).
        """
        file_bodies: dict[str, str] = {}
        for repo_rel in by_dir.get(dir_rel, []):
            file_bodies[repo_rel] = await _file_body(repo_rel)
        sub_bodies = dict(dir_bodies)
        for cand in all_dirs:
            if _dir_of(cand) == dir_rel and cand not in sub_bodies:
                sub_bodies[cand] = _reused_dir_body(cand)
        return _dir_children(
            dir_rel, by_dir=by_dir, file_bodies=file_bodies, dir_bodies=sub_bodies
        )

    # Bottom-up re-roll of the affected DIR.md chain, deepest first.
    dir_bodies: dict[str, str] = {}
    for dir_rel in sorted(affected_dirs, key=lambda d: d.count("/"), reverse=True):
        children = await _build_children(dir_rel, dir_bodies)
        body = await dir_summary(summarizer.client, dir_rel, children)
        dir_bodies[dir_rel] = body
        render_dir_summary(map_root, dir_rel, body)
        store_mod.snapshot_rollup(
            db, dir_rel, head_sha, "dir", body, summary_tokens=count_tokens(body)
        )

    # ROOT is always re-rolled: root-level files + TOP-LEVEL dir bodies. A
    # top-level dir re-rolled this run uses its fresh body; otherwise the
    # latest DB row.
    root_file_bodies: dict[str, str] = {}
    for repo_rel in by_dir.get("", []):
        root_file_bodies[repo_rel] = await _file_body(repo_rel)
    top_dir_bodies = dict(dir_bodies)
    for cand in all_dirs:
        if "/" not in cand and cand not in top_dir_bodies:
            top_dir_bodies[cand] = _reused_dir_body(cand)
    root_children = _root_children(
        by_dir=by_dir, file_bodies=root_file_bodies, dir_bodies=top_dir_bodies
    )
    root_body = await root_summary(summarizer.client, root_children)
    render_root(map_root, root_body)
    store_mod.snapshot_rollup(
        db, "ROOT", head_sha, "root", root_body, summary_tokens=count_tokens(root_body)
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
