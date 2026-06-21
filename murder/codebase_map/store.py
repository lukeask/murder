"""DB persistence for the codebase map (t060).

Raw sqlite (the repo's DB layer is raw sqlite — no ORM). Snapshots each
file/dir/root summary keyed by ``(path, commit_sha)`` so the DB is the
canonical history: "what did the map look like at commit X". Disk
``.murder/map/`` is the live working copy (t059); this is the durable record.

Upsert semantics on the ``(path, commit_sha)`` PK: re-running a build at the
same SHA replaces rows, it does not duplicate them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from murder.codebase_map.summarize import FileSummary


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def snapshot_file(
    db: sqlite3.Connection,
    path: str,
    commit_sha: str,
    summary: FileSummary,
) -> None:
    """Upsert a file summary row, carrying source_hash + token counts."""
    db.execute(
        """
        INSERT INTO map_summaries
            (path, commit_sha, kind, body, source_hash,
             source_tokens, summary_tokens, generated_at)
        VALUES (?, ?, 'file', ?, ?, ?, ?, ?)
        ON CONFLICT(path, commit_sha) DO UPDATE SET
            kind = excluded.kind,
            body = excluded.body,
            source_hash = excluded.source_hash,
            source_tokens = excluded.source_tokens,
            summary_tokens = excluded.summary_tokens,
            generated_at = excluded.generated_at
        """,
        (
            path,
            commit_sha,
            summary.body,
            summary.source_hash,
            summary.source_tokens,
            summary.summary_tokens,
            _now(),
        ),
    )


def snapshot_rollup(
    db: sqlite3.Connection,
    path: str,
    commit_sha: str,
    kind: str,
    body: str,
    *,
    summary_tokens: int,
) -> None:
    """Upsert a dir/root roll-up row (no source_hash/source_tokens)."""
    db.execute(
        """
        INSERT INTO map_summaries
            (path, commit_sha, kind, body, source_hash,
             source_tokens, summary_tokens, generated_at)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
        ON CONFLICT(path, commit_sha) DO UPDATE SET
            kind = excluded.kind,
            body = excluded.body,
            source_hash = excluded.source_hash,
            source_tokens = excluded.source_tokens,
            summary_tokens = excluded.summary_tokens,
            generated_at = excluded.generated_at
        """,
        (path, commit_sha, kind, body, summary_tokens, _now()),
    )


def load_summary(
    db: sqlite3.Connection,
    path: str,
    commit_sha: str,
) -> sqlite3.Row | None:
    """Return the row for ``(path, commit_sha)`` or None."""
    return db.execute(
        "SELECT * FROM map_summaries WHERE path = ? AND commit_sha = ?",
        (path, commit_sha),
    ).fetchone()


def load_latest_summary(
    db: sqlite3.Connection,
    path: str,
) -> sqlite3.Row | None:
    """The most-recently-generated row for ``path``, any commit_sha.

    Incremental updates snapshot ONLY changed/re-rolled paths under the new
    head sha, so an unchanged sibling's newest row may sit several map shas
    back — read-back must take the latest known row, not an exact-sha hit.
    ``rowid DESC`` breaks generated_at ties (seconds resolution).
    """
    return db.execute(
        """
        SELECT * FROM map_summaries
         WHERE path = ?
         ORDER BY generated_at DESC, rowid DESC
         LIMIT 1
        """,
        (path,),
    ).fetchone()


def latest_map_sha(db: sqlite3.Connection) -> str | None:
    """Most-recently-generated commit_sha present (the hinge for t061's diff)."""
    row = db.execute(
        """
        SELECT commit_sha
          FROM map_summaries
         GROUP BY commit_sha
         ORDER BY MAX(generated_at) DESC
         LIMIT 1
        """
    ).fetchone()
    return row["commit_sha"] if row else None


def rows_for_commit(db: sqlite3.Connection, commit_sha: str) -> list[sqlite3.Row]:
    """All map rows for a given commit_sha."""
    return db.execute(
        "SELECT * FROM map_summaries WHERE commit_sha = ? ORDER BY path",
        (commit_sha,),
    ).fetchall()


def all_file_paths(db: sqlite3.Connection) -> set[str]:
    """Every distinct ``path`` that has ever been snapshotted as a file summary.

    The reconcile loop diffs this against the live working tree to find files
    that were summarized once but have since been deleted (their rendered nodes
    must be pruned). Roll-up rows (``kind in ('dir','root')``) are excluded.
    """
    rows = db.execute(
        "SELECT DISTINCT path FROM map_summaries WHERE kind = 'file'"
    ).fetchall()
    return {r["path"] for r in rows}


def prune_file_snapshots(db: sqlite3.Connection, path: str) -> None:
    """Drop the file-summary history for ``path`` — the deletion tombstone.

    When a tracked file is deleted, ``reconcile_map`` re-rolls its parent dir /
    ROOT to drop the now-vanished child. But the path lingers in
    ``map_summaries`` history, so ``all_file_paths`` keeps reporting it as a
    deletion every tick and the rollups would re-fire forever. Removing the
    ``kind='file'`` rows is the durable "this deletion has been reconciled"
    marker: the path drops out of :func:`all_file_paths`, so the deletion is
    processed exactly once. The live map no longer references it once the
    rollups have been re-rolled, so dropping the history is safe.
    """
    db.execute(
        "DELETE FROM map_summaries WHERE path = ? AND kind = 'file'",
        (path,),
    )


__all__ = [
    "snapshot_file",
    "snapshot_rollup",
    "load_summary",
    "load_latest_summary",
    "latest_map_sha",
    "rows_for_commit",
    "all_file_paths",
    "prune_file_snapshots",
]
