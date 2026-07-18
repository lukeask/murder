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
import hashlib
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

# Hard ceiling: never hand a file this big (bytes) to the summarizer. A 250 KB
# file is ~60k tokens by the map's len//4 estimate — it blows free-tier context
# windows, derives an enormous output budget, and is almost always generated.
_MAX_SUMMARY_BYTES = 250 * 1024

# Machine-generated / data files with no summarization value. Summarizing them
# is pure API waste and clutters the map, so they are excluded entirely (and any
# previously rendered node is pruned on the next reconcile).
_GENERATED_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "cargo.lock", "composer.lock", "gemfile.lock", "go.sum",
})
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".jsonl")
# Any path segment naming a test-data / snapshot tree — its contents are
# fixtures, not source worth mapping.
_FIXTURE_DIRS = frozenset({
    "fixtures", "__fixtures__", "snapshots", "__snapshots__",
    "testdata", "test_data", "cassettes",
})


def _is_generated_or_fixture(repo_rel: str) -> bool:
    """True for lockfiles, minified bundles, sourcemaps, ``.jsonl`` streams and
    anything under a fixtures/snapshots/testdata tree — none worth summarizing."""
    path = Path(repo_rel)
    low = repo_rel.lower()
    if path.name.lower() in _GENERATED_NAMES:
        return True
    if low.endswith(_GENERATED_SUFFIXES) or low.endswith("-lock.json"):
        return True
    return bool(_FIXTURE_DIRS.intersection(part.lower() for part in path.parts))


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


def _mappable(repo_root: Path, repo_rel: str) -> bool:
    """Should this tracked file appear in the map at all?

    Combines every pre-summarize blocker: non-text extensions, generated/fixture
    files, and the hard byte ceiling. Files that fail are excluded from the
    tracked set entirely — never summarized, and any stale rendered node is
    pruned on the next reconcile. A file that vanished between ``ls-files`` and
    the ``stat`` is treated as non-mappable (it is gone anyway)."""
    if not _is_text(repo_rel) or _is_generated_or_fixture(repo_rel):
        return False
    try:
        return (repo_root / repo_rel).stat().st_size <= _MAX_SUMMARY_BYTES
    except OSError:
        return False


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


async def _summarize_or_reuse(
    summarizer: FileSummarizer,
    db: sqlite3.Connection | None,
    repo_rel: str,
    src: str,
) -> FileSummary:
    """Summarize ``repo_rel`` — but reuse the persisted body when the source is
    byte-identical to what we last summarized (the per-file staleness guard).

    The expensive LLM call is skipped whenever ``load_latest_summary`` returns a
    row whose ``source_hash`` matches ``sha256(src)``. This is what makes a
    re-run cheap: already-summarized files cost a hash compare + a DB read, not
    a model call. Returns a :class:`FileSummary` either way so the caller can
    render + re-snapshot it under the current head uniformly.
    """
    src_hash = hashlib.sha256(src.encode()).hexdigest()
    if db is not None:
        from murder.codebase_map import store as store_mod

        prior = store_mod.load_latest_summary(db, repo_rel)
        if prior is not None and prior["source_hash"] == src_hash:
            return FileSummary(
                path=repo_rel,
                body=prior["body"] or "",
                source_hash=src_hash,
                source_tokens=prior["source_tokens"] or count_tokens(src),
                summary_tokens=prior["summary_tokens"] or 0,
            )
    return await summarizer.summarize(repo_rel, src)


async def _apply_changeset(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection | None,
    head_sha: str | None,
    present: list[str],
    deleted: list[str],
    render_only: list[str] | None = None,
    repair_dirs: set[str] | None = None,
    repair_root: bool = False,
    sources: dict[str, str] | None = None,
    concurrency: int = 8,
) -> None:
    """Apply a file-level changeset to the map, streaming each result to disk
    (and the DB) as it completes so the build is resumable.

    This is the shared engine behind :func:`fresh_build` (everything present),
    :func:`incremental_update` (git-diff present/deleted) and
    :func:`reconcile_map` (content-hash present/deleted + repair). It:

    - (re)summarizes ``present`` files — skipping the LLM for any whose source
      hash already matches its latest snapshot (:func:`_summarize_or_reuse`) —
      and renders + snapshots each one *immediately* (resumable);
    - re-renders ``render_only`` files from their canonical DB body (disk was
      missing/stale but the summary is current — no model call);
    - removes rendered nodes for ``deleted`` files (and tombstones their
      file-summary history so the deletion is not re-processed every tick);
    - re-rolls the ancestor ``DIR.md`` chains of every changed/deleted path
      (plus any ``repair_dirs`` whose roll-up went missing), reusing unchanged
      sibling bodies from the DB, then ROOT.

    A directory left with no tracked text files (last file deleted) has its
    ``DIR.md`` removed instead of being re-rolled empty.

    ROOT is re-rolled only when there is genuine rollup work — any ``present``,
    ``deleted`` or ``affected_dirs``, or ``repair_root`` (ROOT.md missing / no
    ROOT snapshot). A pure ``render_only`` repair (a ``<file>.md`` re-rendered
    from a current DB body) changes no rollup content and so makes NO model
    call at all — ROOT is skipped.
    """
    repo_root = Path(repo_root)
    map_root = map_root_for(repo_root)
    present = list(present)
    deleted = list(deleted)
    render_only = list(render_only or [])
    repair_dirs = set(repair_dirs or set())

    from murder.codebase_map import store as store_mod

    # Read sources for present files (caller may pre-supply them).
    if sources is None:
        sources = {}
        for repo_rel in present:
            try:
                sources[repo_rel] = (repo_root / repo_rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                # Vanished / undeclared-binary between selection and read —
                # treat as deleted so its stale node is removed.
                deleted.append(repo_rel)
        present = [p for p in present if p in sources]
    else:
        sources = {p: sources[p] for p in present if p in sources}
        for repo_rel in present:
            if repo_rel not in sources:
                deleted.append(repo_rel)
        present = list(sources)

    # Fan out summaries under a semaphore, STREAMING each result to disk + DB as
    # it lands (resumable: an interrupted build leaves every completed file
    # persisted). Pre-warm with one before gather() to dodge the
    # ChatCompletionsClient lazy-init race.
    fresh_file_bodies: dict[str, str] = {}

    async def _do_one(repo_rel: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            summary = await _summarize_or_reuse(summarizer, db, repo_rel, sources[repo_rel])
        render_file_summary(map_root, repo_rel, summary)
        if db is not None and head_sha is not None:
            store_mod.snapshot_file(db, repo_rel, head_sha, summary)
        fresh_file_bodies[repo_rel] = summary.body

    if present:
        sem = asyncio.Semaphore(concurrency)
        await _do_one(present[0], sem)
        if len(present) > 1:
            results = await asyncio.gather(
                *(_do_one(p, sem) for p in present[1:]), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    # Re-render files whose body is current in the DB but whose <file>.md went
    # missing on disk (no model call — pure repair from the canonical record).
    for repo_rel in render_only:
        row = store_mod.load_latest_summary(db, repo_rel) if db is not None else None
        if row is not None:
            render_file_summary(
                map_root,
                repo_rel,
                FileSummary(
                    path=repo_rel,
                    body=row["body"] or "",
                    source_hash=row["source_hash"] or "",
                    source_tokens=row["source_tokens"] or 0,
                    summary_tokens=row["summary_tokens"] or 0,
                ),
            )

    # Remove rendered nodes for deletions and tombstone their file-summary
    # history. Pruning the history (not relying on the now best-effort .md
    # unlink) is the re-fire guard: once the path leaves ``all_file_paths`` the
    # deletion is processed exactly once even if its render was already missing.
    for repo_rel in deleted:
        target = map_root / (repo_rel + ".md")
        if target.exists():
            target.unlink()
        if db is not None:
            store_mod.prune_file_snapshots(db, repo_rel)

    # Current mappable files grouped by dir; the full closure tells us, for any
    # re-rolled dir, its complete set of immediate subdirectories (including
    # unchanged ones we must re-feed).
    tracked_now = {p for p in await _git_ls_files(repo_root) if _mappable(repo_root, p)}
    by_dir: dict[str, list[str]] = {}
    for repo_rel in sorted(tracked_now):
        by_dir.setdefault(_dir_of(repo_rel), []).append(repo_rel)
    all_dirs = _dir_closure(list(tracked_now))

    # Dirs to re-roll: the ancestor chains of every changed/deleted path, plus
    # any caller-requested repair dirs. A dir that no longer holds ANY tracked
    # text file (last file deleted) has vanished: remove its DIR.md instead of
    # re-rolling an empty node, deepest first.
    affected_dirs = _dir_closure(present + deleted) | repair_dirs
    vanished_dirs = affected_dirs - all_dirs
    affected_dirs &= all_dirs
    for dir_rel in sorted(vanished_dirs, key=lambda d: d.count("/"), reverse=True):
        target = map_root / dir_rel / "DIR.md"
        if target.exists():
            target.unlink()
        with contextlib.suppress(OSError):
            (map_root / dir_rel).rmdir()

    # Body of a file child — fresh if (re)summarized this run, else the latest
    # DB row (canonical), else a fresh roll from disk.
    async def _file_body(repo_rel: str) -> str:
        if repo_rel in fresh_file_bodies:
            return fresh_file_bodies[repo_rel]
        row = store_mod.load_latest_summary(db, repo_rel) if db is not None else None
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
        row = store_mod.load_latest_summary(db, dir_rel) if db is not None else None
        return (row["body"] if row is not None else "") or ""

    async def _build_children(dir_rel: str, dir_bodies: dict[str, str]) -> list[ChildEntry]:
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
        body = await dir_summary(summarizer.rollup_client, dir_rel, children)
        dir_bodies[dir_rel] = body
        render_dir_summary(map_root, dir_rel, body)
        if db is not None and head_sha is not None:
            store_mod.snapshot_rollup(
                db, dir_rel, head_sha, "dir", body, summary_tokens=count_tokens(body)
            )

    # Re-roll ROOT only when there is genuine rollup work. A pure render_only
    # (or pure-prune-with-no-parent-change) run leaves all of these empty/false
    # and so makes no ROOT model call — the file body was re-rendered from the
    # DB and no rollup content changed.
    if present or deleted or affected_dirs or repair_root:
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
        root_body = await root_summary(summarizer.rollup_client, root_children)
        render_root(map_root, root_body)
        if db is not None and head_sha is not None:
            store_mod.snapshot_rollup(
                db, "ROOT", head_sha, "root", root_body,
                summary_tokens=count_tokens(root_body),
            )


def _prune_orphans(map_root: Path, tracked: set[str], dirs: set[str]) -> None:
    """Delete rendered nodes that no longer map to a tracked source.

    A ``<rel>.md`` whose source path is no longer tracked, or a ``DIR.md`` for a
    directory no longer in the closure, is removed; then any directory left
    empty is pruned bottom-up. ``ROOT.md`` is always kept.
    """
    if not map_root.exists():
        return
    keep_files = {map_root / (p + ".md") for p in tracked}
    for md in list(map_root.rglob("*.md")):
        if md.name == "ROOT.md":
            continue
        if md.name == "DIR.md":
            rel = str(md.parent.relative_to(map_root))
            rel = "" if rel == "." else rel
            if rel and rel not in dirs:
                md.unlink()
            continue
        if md not in keep_files:
            md.unlink()
    for d in sorted(
        (p for p in map_root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        with contextlib.suppress(OSError):
            d.rmdir()


async def fresh_build(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection | None = None,
    concurrency: int = 8,
) -> None:
    """Regenerate the whole ``.murder/map/`` tree from the working tree.

    Every tracked text file is treated as present; bottom-up roll-ups follow.
    The build is STREAMING and resumable: each file is rendered + snapshotted
    the moment its summary lands, and a file whose source is unchanged since its
    last snapshot reuses that body instead of paying for the model again. Stale
    rendered nodes (sources that vanished) are pruned at the end rather than the
    whole tree being blown away, so existing valid summaries survive a re-run.
    """
    repo_root = Path(repo_root)
    map_root = map_root_for(repo_root)

    head_sha: str | None = None
    if db is not None:
        from murder.verdict.enforcement.git_diff import head_commit

        head_sha = await head_commit(repo_root)

    tracked = [p for p in await _git_ls_files(repo_root) if _mappable(repo_root, p)]
    all_dirs = _dir_closure(tracked)
    await _apply_changeset(
        repo_root,
        summarizer,
        db=db,
        head_sha=head_sha,
        present=tracked,
        deleted=[],
        repair_dirs=set(all_dirs),
        concurrency=concurrency,
    )
    _prune_orphans(map_root, set(tracked), set(all_dirs))


async def incremental_update(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection,
    base_sha: str,
    head_sha: str,
    concurrency: int = 8,
) -> None:
    """Re-summarize only files changed between ``base_sha`` and ``head_sha`` and
    re-roll the ancestor ``DIR.md`` chains.

    A thin wrapper over :func:`_apply_changeset`: the changeset is the git diff.
    Unchanged sibling bodies needed to re-roll a parent are read back from the
    DB via the latest known row for that path.
    """
    from murder.verdict.enforcement.git_diff import changed_files

    changed = [p for p in await changed_files(repo_root, base_sha, head_sha) if _is_text(p)]
    if not changed:
        return

    tracked_now = {p for p in await _git_ls_files(repo_root) if _is_text(p)}
    present: list[str] = []
    deleted: list[str] = []
    for repo_rel in changed:
        if repo_rel in tracked_now and _mappable(repo_root, repo_rel):
            present.append(repo_rel)
        else:
            # Gone from the tree, or now ruled out (too big / generated): prune
            # any stale rendered node rather than (re)summarize it.
            deleted.append(repo_rel)

    await _apply_changeset(
        repo_root,
        summarizer,
        db=db,
        head_sha=head_sha,
        present=present,
        deleted=deleted,
        concurrency=concurrency,
    )


async def reconcile_map(
    repo_root: Path,
    summarizer: FileSummarizer,
    *,
    db: sqlite3.Connection,
    head_sha: str | None = None,
    concurrency: int = 8,
) -> None:
    """Bring the map in line with the working tree, doing the least work needed.

    The single entrypoint the background worker drives every tick. It is keyed
    on PER-FILE content hashes, not a per-codebase commit sha, so it is cheap
    when nothing changed and resumable when a prior build was interrupted:

    - a file with no snapshot, or whose source hash drifted, is (re)summarized;
    - a file whose summary is current but whose ``<file>.md`` went missing is
      re-rendered from the DB (no model call);
    - a path that was summarized once but is no longer tracked is pruned;
    - directories/ROOT whose roll-ups went missing are repaired.

    When everything is already current this performs zero model calls and
    returns after a hash scan, which is what stops the worker from re-burning
    the API on every launch.
    """
    repo_root = Path(repo_root)
    map_root = map_root_for(repo_root)

    if head_sha is None:
        from murder.verdict.enforcement.git_diff import head_commit

        head_sha = await head_commit(repo_root)

    from murder.codebase_map import store as store_mod

    tracked = [p for p in await _git_ls_files(repo_root) if _mappable(repo_root, p)]
    sources: dict[str, str] = {}
    for repo_rel in tracked:
        try:
            sources[repo_rel] = (repo_root / repo_rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    tracked_set = set(sources)

    present: list[str] = []      # missing snapshot or stale hash -> (re)summarize
    render_only: list[str] = []  # snapshot current but <file>.md missing -> re-render
    for repo_rel, src in sources.items():
        src_hash = hashlib.sha256(src.encode()).hexdigest()
        row = store_mod.load_latest_summary(db, repo_rel)
        current = row is not None and row["source_hash"] == src_hash
        on_disk = (map_root / (repo_rel + ".md")).exists()
        if not current:
            present.append(repo_rel)
        elif not on_disk:
            render_only.append(repo_rel)

    # Files we summarized once but that are no longer tracked. NOT gated on the
    # rendered <file>.md still existing: a deletion whose render was already
    # missing must still re-roll its parents to drop the vanished child.
    # Re-firing is prevented by the DB tombstone instead — _apply_changeset
    # prunes the file-summary history for each deleted path, so it leaves
    # ``all_file_paths`` after this run (one re-roll, not one per tick forever).
    deleted = sorted(store_mod.all_file_paths(db) - tracked_set)

    # Roll-ups whose nodes went missing (resume a build interrupted mid-roll).
    all_dirs = _dir_closure(tracked)
    repair_dirs: set[str] = set()
    for dir_rel in all_dirs:
        if not (map_root / dir_rel / "DIR.md").exists() or (
            store_mod.load_latest_summary(db, dir_rel) is None
        ):
            repair_dirs.add(dir_rel)
    repair_root = (
        not (map_root / "ROOT.md").exists()
        or store_mod.load_latest_summary(db, "ROOT") is None
    )

    if not (present or deleted or render_only or repair_dirs or repair_root):
        _prune_orphans(map_root, tracked_set, set(all_dirs))
        return

    await _apply_changeset(
        repo_root,
        summarizer,
        db=db,
        head_sha=head_sha,
        present=present,
        deleted=deleted,
        render_only=render_only,
        repair_dirs=repair_dirs,
        repair_root=repair_root,
        sources={p: sources[p] for p in present},
        concurrency=concurrency,
    )
    _prune_orphans(map_root, tracked_set, set(all_dirs))


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
