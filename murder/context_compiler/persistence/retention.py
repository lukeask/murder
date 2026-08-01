"""Snapshot retention and garbage collection for the context-index DB.

Retention policy
-----------------
Per worktree, only the newest **two** ``ready`` snapshots are kept. "Newest"
always means ``state_timestamp DESC, snapshot_id DESC`` — the same ordering
used everywhere else in this package (see
:mod:`murder.context_compiler.persistence.snapshots`) — never
``generated_at``. ``state_timestamp`` identifies the on-disk repository state
a snapshot represents; ``generated_at`` only records when indexing finished
and can lag behind for a snapshot of an older state that happened to be
(re)built later. Ordering retention by ``generated_at`` could therefore keep
a stale state and discard a newer one.

``building`` and ``failed`` snapshots never count toward the two-``ready``
budget and ordinary ready-retention (:func:`apply_retention`) never inspects
or deletes them — a snapshot currently being built stays untouched no matter
how many ``ready`` snapshots exist. Stale ``building``/``failed`` rows (e.g.
abandoned or crashed indexing attempts) are pruned separately, and only on
an explicit, caller-supplied age cutoff (see :func:`cleanup_non_ready_snapshots`)
rather than any policy this module invents on its own.

Garbage collection
-------------------
Deleting a snapshot removes its ``snapshot_files`` rows (``ON DELETE
CASCADE``) but never directly touches ``file_versions``, since
``snapshot_files.file_version_id`` is ``ON DELETE RESTRICT`` — a file version
can only be deleted once nothing references it. :func:`garbage_collect`
(and the GC step inside :func:`apply_retention`) walks outward from there:

1. Delete ``file_versions`` no longer referenced by any ``snapshot_files``
   row. This cascades (``ON DELETE CASCADE``) to that version's own
   ``semantic_unit_versions``, and from there to ``imports``,
   ``"references"`` (and their ``reference_targets``), ``relationships``
   sourced from the version, and ``resource_links`` sourced from its units.
2. Delete logical ``semantic_units`` rows left with no remaining
   ``semantic_unit_versions`` — **provided** nothing still-retained points
   at them: not ``relationships.target_unit_id``, not
   ``reference_targets.target_unit_id``, and not another retained
   ``semantic_unit_versions.parent_unit_id``. Logical identity is a stable
   handle that outside edges point at, so a unit with no live version but a
   live inbound edge must survive.
3. Delete logical ``files`` rows left with no remaining ``file_versions``,
   no ``snapshot_files`` rows, no remaining ``semantic_units`` of their own,
   and not referenced by ``relationships.target_file_id`` or
   ``resource_links.target_file_id``.

Each step is deliberately conservative: identity rows (``files``,
``semantic_units``) are only ever deleted when *nothing* retained — content,
edge, or otherwise — still points at them. It is always safe to garbage
collect less than the theoretical maximum; it is never safe to delete a
logical row an edge still targets, since edges intentionally target logical
identities (not versions) so they survive ordinary edits.

All multi-statement work here runs inside :func:`~murder.context_compiler.
persistence.connection.transaction`, since the connection is opened with
``isolation_level=None`` (autocommit) and has no implicit transaction of its
own.
"""

from __future__ import annotations

import sqlite3

from murder.context_compiler.persistence.connection import transaction
from murder.context_compiler.persistence.records import RetentionResult
from murder.context_compiler.persistence.snapshots import delete_snapshot, list_ready_snapshots


def apply_retention(
    conn: sqlite3.Connection,
    worktree_id: int,
    *,
    keep_ready: int = 2,
) -> RetentionResult:
    """Prune old ``ready`` snapshots for ``worktree_id`` and garbage collect.

    Lists ``ready`` snapshots newest-first (``state_timestamp DESC,
    snapshot_id DESC``), deletes every one beyond the newest ``keep_ready``,
    then runs worktree-scoped garbage collection so versions/units/files that
    were only reachable from the deleted snapshots are cleaned up too.
    ``building``/``failed`` snapshots are never listed or deleted here — they
    do not count toward ``keep_ready`` and a snapshot currently being built
    is never touched by ordinary ready retention.

    Raises ``ValueError`` if ``keep_ready`` is negative.
    """
    if keep_ready < 0:
        raise ValueError(f"keep_ready must be >= 0, got {keep_ready}")

    with transaction(conn):
        ready = list_ready_snapshots(conn, worktree_id)
        to_prune = ready[keep_ready:]
        for snapshot in to_prune:
            delete_snapshot(conn, snapshot.snapshot_id)

        gc_result = _run_garbage_collection(conn, worktree_id=worktree_id)

    return RetentionResult(
        deleted_snapshots=len(to_prune),
        deleted_file_versions=gc_result.deleted_file_versions,
        deleted_semantic_unit_versions=gc_result.deleted_semantic_unit_versions,
        deleted_semantic_units=gc_result.deleted_semantic_units,
        deleted_files=gc_result.deleted_files,
    )


def garbage_collect(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None = None,
) -> RetentionResult:
    """Delete content/identity rows unreachable from any remaining snapshot.

    Does not touch ``snapshots`` itself (``deleted_snapshots`` is always
    ``0``); it only clears out rows that became orphaned by snapshot
    deletions that already happened, whenever or however they happened.
    When ``worktree_id`` is given, only files/units belonging to that
    worktree are considered — file versions, units, and files belonging to
    other worktrees are left untouched. When ``None``, GC runs globally
    across every worktree in the database.
    """
    with transaction(conn):
        return _run_garbage_collection(conn, worktree_id=worktree_id)


def cleanup_non_ready_snapshots(
    conn: sqlite3.Connection,
    worktree_id: int,
    *,
    older_than: str,
    statuses: tuple[str, ...] = ("building", "failed"),
) -> int:
    """Delete stale ``building``/``failed`` snapshots for a worktree.

    ``older_than`` is an ISO-8601 timestamp string compared lexically against
    ``generated_at``; only snapshots with ``generated_at < older_than`` are
    deleted. This module never invents its own "stale" cutoff — a snapshot
    stuck in ``building`` might simply be in progress, and only the caller
    (which knows how long indexing should reasonably take, and can check
    whether the producing process is still alive) can pick a safe cutoff.

    After deleting the matching snapshots, this also runs worktree-scoped
    garbage collection in the same transaction so file versions that were
    only attached to the deleted snapshots don't linger — but the *counts*
    from that cleanup are not returned here. Call :func:`garbage_collect` or
    :func:`apply_retention` separately if those counts are needed; this
    function's return value is always just the number of snapshot rows
    deleted, per its signature.

    Returns the number of snapshots deleted. Returns ``0`` immediately
    without touching the database if ``statuses`` is empty.
    """
    if not statuses:
        return 0

    with transaction(conn):
        placeholders = ",".join("?" for _ in statuses)
        cursor = conn.execute(
            f"""
            DELETE FROM snapshots
             WHERE worktree_id = ?
               AND status IN ({placeholders})
               AND generated_at < ?
            """,
            (worktree_id, *statuses, older_than),
        )
        deleted = cursor.rowcount if cursor.rowcount >= 0 else 0

        _run_garbage_collection(conn, worktree_id=worktree_id)

    return deleted


def _run_garbage_collection(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None,
) -> RetentionResult:
    """Do the actual GC work. Caller must already hold an open transaction."""
    deleted_file_versions, deleted_semantic_unit_versions = _delete_unreferenced_file_versions(
        conn, worktree_id=worktree_id
    )
    deleted_semantic_units = _delete_orphan_semantic_units(conn, worktree_id=worktree_id)
    deleted_files = _delete_orphan_files(conn, worktree_id=worktree_id)
    return RetentionResult(
        deleted_snapshots=0,
        deleted_file_versions=deleted_file_versions,
        deleted_semantic_unit_versions=deleted_semantic_unit_versions,
        deleted_semantic_units=deleted_semantic_units,
        deleted_files=deleted_files,
    )


def _unreferenced_file_version_ids(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None,
) -> list[int]:
    """File versions with no remaining ``snapshot_files`` row."""
    if worktree_id is None:
        rows = conn.execute(
            """
            SELECT fv.file_version_id
              FROM file_versions AS fv
             WHERE NOT EXISTS (
                 SELECT 1 FROM snapshot_files AS sf
                  WHERE sf.file_version_id = fv.file_version_id
             )
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT fv.file_version_id
              FROM file_versions AS fv
              JOIN files AS f ON f.file_id = fv.file_id
             WHERE f.worktree_id = ?
               AND NOT EXISTS (
                   SELECT 1 FROM snapshot_files AS sf
                    WHERE sf.file_version_id = fv.file_version_id
               )
            """,
            (worktree_id,),
        ).fetchall()
    return [int(row["file_version_id"]) for row in rows]


def _delete_unreferenced_file_versions(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None,
) -> tuple[int, int]:
    """Delete orphaned file versions; return (versions, cascaded unit versions).

    ``semantic_unit_versions`` are ``ON DELETE CASCADE`` from
    ``file_versions``, so the DELETE below removes them implicitly along
    with the imports/references/relationships/resource-links sourced from
    those versions. The unit-version count is taken with a ``COUNT(*)``
    just before the delete, since ``cursor.rowcount`` only reflects rows
    matched by the statement's own WHERE clause, not cascaded rows.
    """
    file_version_ids = _unreferenced_file_version_ids(conn, worktree_id=worktree_id)
    if not file_version_ids:
        return 0, 0

    placeholders = ",".join("?" for _ in file_version_ids)
    unit_version_row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM semantic_unit_versions
         WHERE file_version_id IN ({placeholders})
        """,
        file_version_ids,
    ).fetchone()
    deleted_unit_versions = int(unit_version_row["n"])

    conn.execute(
        f"DELETE FROM file_versions WHERE file_version_id IN ({placeholders})",
        file_version_ids,
    )
    return len(file_version_ids), deleted_unit_versions


def _delete_orphan_semantic_units(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None,
) -> int:
    """Delete logical units with no remaining version and no inbound edge.

    A unit survives if any of the following still exists:
    * a ``semantic_unit_versions`` row for it (it still has live content),
    * a ``relationships.target_unit_id`` row pointing at it,
    * a ``reference_targets.target_unit_id`` row pointing at it, or
    * another remaining ``semantic_unit_versions.parent_unit_id`` pointing
      at it (it is still someone's live parent).
    """
    worktree_clause = "AND f.worktree_id = ?" if worktree_id is not None else ""
    params: tuple[int, ...] = (worktree_id,) if worktree_id is not None else ()
    cursor = conn.execute(
        f"""
        DELETE FROM semantic_units
         WHERE unit_id IN (
             SELECT su.unit_id
               FROM semantic_units AS su
               JOIN files AS f ON f.file_id = su.file_id
              WHERE NOT EXISTS (
                        SELECT 1 FROM semantic_unit_versions AS suv
                         WHERE suv.unit_id = su.unit_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM relationships AS r
                         WHERE r.target_unit_id = su.unit_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM reference_targets AS rt
                         WHERE rt.target_unit_id = su.unit_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM semantic_unit_versions AS suv2
                         WHERE suv2.parent_unit_id = su.unit_id
                    )
                {worktree_clause}
         )
        """,
        params,
    )
    return cursor.rowcount if cursor.rowcount >= 0 else 0


def _delete_orphan_files(
    conn: sqlite3.Connection,
    *,
    worktree_id: int | None,
) -> int:
    """Delete logical files with no remaining content, unit, or inbound edge.

    A file survives if any of the following still exists:
    * a ``file_versions`` row for it,
    * a ``snapshot_files`` row for it (defensive: normally implied by a
      surviving file version, but checked directly too),
    * a ``semantic_units`` row for it — deleting the file would cascade
      (``ON DELETE CASCADE``) and remove a logical unit that survived GC
      because something still targets it, which would violate the
      conservative deletion guarantee,
    * a ``relationships.target_file_id`` row pointing at it, or
    * a ``resource_links.target_file_id`` row pointing at it.
    """
    worktree_clause = "AND f.worktree_id = ?" if worktree_id is not None else ""
    params: tuple[int, ...] = (worktree_id,) if worktree_id is not None else ()
    cursor = conn.execute(
        f"""
        DELETE FROM files
         WHERE file_id IN (
             SELECT f.file_id
               FROM files AS f
              WHERE NOT EXISTS (
                        SELECT 1 FROM file_versions AS fv
                         WHERE fv.file_id = f.file_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM snapshot_files AS sf
                         WHERE sf.file_id = f.file_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM semantic_units AS su
                         WHERE su.file_id = f.file_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM relationships AS r
                         WHERE r.target_file_id = f.file_id
                    )
                AND NOT EXISTS (
                        SELECT 1 FROM resource_links AS rl
                         WHERE rl.target_file_id = f.file_id
                    )
                {worktree_clause}
         )
        """,
        params,
    )
    return cursor.rowcount if cursor.rowcount >= 0 else 0


__all__ = [
    "apply_retention",
    "cleanup_non_ready_snapshots",
    "garbage_collect",
]
