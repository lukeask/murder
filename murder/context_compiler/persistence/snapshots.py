"""Worktree and snapshot lifecycle for the experimental context-index DB.

A ``snapshot`` has two distinct timestamps:

* ``state_timestamp`` identifies the on-disk repository state the snapshot
  represents (when the files being indexed were observed).
* ``generated_at`` records when indexing of that state finished (or, for
  ``building``/``failed`` rows, when the attempt started/last changed status).

Newest-state selection ALWAYS orders by ``state_timestamp DESC,
snapshot_id DESC`` — never ``generated_at``. An older repository state whose
indexing happens to finish later must not supersede a newer state that
finished indexing sooner.

A snapshot's ``status`` moves through ``building -> ready`` or
``building -> failed``; both are terminal. Only ``ready`` snapshots
participate in current-state queries (:func:`list_ready_snapshots`,
:func:`get_newest_ready_snapshot`, :func:`get_previous_ready_snapshot`,
:func:`get_current_and_previous_ready`). ``building`` and ``failed``
snapshots are invisible to those queries; they exist only so callers can
track in-flight or failed indexing attempts.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from murder.context_compiler.persistence.connection import transaction
from murder.context_compiler.persistence.records import (
    CurrentPreviousSnapshots,
    SnapshotRecord,
    WorktreeRecord,
)


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _canonicalize(path: Path | str) -> str:
    return str(Path(path).resolve())


def get_or_create_worktree(
    conn: sqlite3.Connection,
    *,
    repository_root: Path | str,
    worktree_root: Path | str,
    seen_at: str | None = None,
) -> WorktreeRecord:
    """Upsert a worktree keyed on ``(repository_root, worktree_root)``.

    Both roots are canonicalized via ``Path.resolve()`` before matching or
    storing. An existing row has its ``last_seen_at`` bumped; a new row gets
    ``created_at`` and ``last_seen_at`` set to the same timestamp.
    """
    root = _canonicalize(repository_root)
    worktree = _canonicalize(worktree_root)
    timestamp = seen_at if seen_at is not None else _now()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO worktrees (repository_root, worktree_root, created_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repository_root, worktree_root) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (root, worktree, timestamp, timestamp),
        )
        row = conn.execute(
            """
            SELECT * FROM worktrees
             WHERE repository_root = ? AND worktree_root = ?
            """,
            (root, worktree),
        ).fetchone()
    assert row is not None
    return _row_to_worktree(row)


def create_building_snapshot(
    conn: sqlite3.Connection,
    *,
    worktree_id: int,
    state_timestamp: str,
    commit_sha: str | None = None,
    generated_at: str | None = None,
) -> SnapshotRecord:
    """Insert a new snapshot in ``building`` status with no failure reason."""
    timestamp = generated_at if generated_at is not None else _now()
    cursor = conn.execute(
        """
        INSERT INTO snapshots (
            worktree_id, state_timestamp, commit_sha, status, generated_at, failure_reason
        ) VALUES (?, ?, ?, 'building', ?, NULL)
        """,
        (worktree_id, state_timestamp, commit_sha, timestamp),
    )
    snapshot_id = cursor.lastrowid
    assert snapshot_id is not None
    record = get_snapshot(conn, snapshot_id)
    assert record is not None
    return record


def mark_snapshot_ready(
    conn: sqlite3.Connection,
    snapshot_id: int,
    *,
    generated_at: str | None = None,
) -> SnapshotRecord:
    """Transition a snapshot ``building -> ready``.

    Raises ``ValueError`` if the snapshot does not exist or is not currently
    ``building``.
    """
    timestamp = generated_at if generated_at is not None else _now()
    with transaction(conn):
        current = get_snapshot(conn, snapshot_id)
        if current is None:
            raise ValueError(f"snapshot {snapshot_id} does not exist")
        if current.status != "building":
            raise ValueError(f"snapshot {snapshot_id} is {current.status!r}, expected 'building'")
        conn.execute(
            """
            UPDATE snapshots
               SET status = 'ready', generated_at = ?, failure_reason = NULL
             WHERE snapshot_id = ?
            """,
            (timestamp, snapshot_id),
        )
        record = get_snapshot(conn, snapshot_id)
    assert record is not None
    return record


def mark_snapshot_failed(
    conn: sqlite3.Connection,
    snapshot_id: int,
    failure_reason: str,
    *,
    generated_at: str | None = None,
) -> SnapshotRecord:
    """Transition a snapshot ``building -> failed`` with a non-empty reason.

    Raises ``ValueError`` if the snapshot does not exist, is not currently
    ``building``, or ``failure_reason`` is empty.
    """
    if not failure_reason:
        raise ValueError("failure_reason must not be empty")
    timestamp = generated_at if generated_at is not None else _now()
    with transaction(conn):
        current = get_snapshot(conn, snapshot_id)
        if current is None:
            raise ValueError(f"snapshot {snapshot_id} does not exist")
        if current.status != "building":
            raise ValueError(f"snapshot {snapshot_id} is {current.status!r}, expected 'building'")
        conn.execute(
            """
            UPDATE snapshots
               SET status = 'failed', generated_at = ?, failure_reason = ?
             WHERE snapshot_id = ?
            """,
            (timestamp, failure_reason, snapshot_id),
        )
        record = get_snapshot(conn, snapshot_id)
    assert record is not None
    return record


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> SnapshotRecord | None:
    """Return the snapshot by id, regardless of status, or ``None``."""
    row = conn.execute(
        "SELECT * FROM snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def list_ready_snapshots(
    conn: sqlite3.Connection,
    worktree_id: int,
    *,
    limit: int | None = None,
) -> list[SnapshotRecord]:
    """List ``ready`` snapshots for a worktree, newest state first.

    Ordered by ``state_timestamp DESC, snapshot_id DESC`` — never
    ``generated_at``.
    """
    if limit is not None:
        rows = conn.execute(
            """
            SELECT * FROM snapshots
             WHERE worktree_id = ? AND status = 'ready'
             ORDER BY state_timestamp DESC, snapshot_id DESC
             LIMIT ?
            """,
            (worktree_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM snapshots
             WHERE worktree_id = ? AND status = 'ready'
             ORDER BY state_timestamp DESC, snapshot_id DESC
            """,
            (worktree_id,),
        ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def get_newest_ready_snapshot(conn: sqlite3.Connection, worktree_id: int) -> SnapshotRecord | None:
    """The newest ``ready`` snapshot for a worktree, or ``None``."""
    snapshots = list_ready_snapshots(conn, worktree_id, limit=1)
    return snapshots[0] if snapshots else None


def get_previous_ready_snapshot(
    conn: sqlite3.Connection, worktree_id: int
) -> SnapshotRecord | None:
    """The second-newest ``ready`` snapshot for a worktree, or ``None``."""
    snapshots = list_ready_snapshots(conn, worktree_id, limit=2)
    return snapshots[1] if len(snapshots) > 1 else None


def get_current_and_previous_ready(
    conn: sqlite3.Connection, worktree_id: int
) -> CurrentPreviousSnapshots:
    """The newest and second-newest ``ready`` snapshots for a worktree."""
    snapshots = list_ready_snapshots(conn, worktree_id, limit=2)
    current = snapshots[0] if snapshots else None
    previous = snapshots[1] if len(snapshots) > 1 else None
    return CurrentPreviousSnapshots(current=current, previous=previous)


def delete_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> None:
    """Delete a snapshot; ``snapshot_files`` rows cascade via the schema."""
    conn.execute(
        "DELETE FROM snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    )


def _row_to_worktree(row: sqlite3.Row) -> WorktreeRecord:
    return WorktreeRecord(
        worktree_id=int(row["worktree_id"]),
        repository_root=str(row["repository_root"]),
        worktree_root=str(row["worktree_root"]),
        created_at=str(row["created_at"]),
        last_seen_at=str(row["last_seen_at"]),
    )


def _row_to_snapshot(row: sqlite3.Row) -> SnapshotRecord:
    return SnapshotRecord(
        snapshot_id=int(row["snapshot_id"]),
        worktree_id=int(row["worktree_id"]),
        state_timestamp=str(row["state_timestamp"]),
        commit_sha=row["commit_sha"],
        status=row["status"],
        generated_at=str(row["generated_at"]),
        failure_reason=row["failure_reason"],
    )


__all__ = [
    "create_building_snapshot",
    "delete_snapshot",
    "get_current_and_previous_ready",
    "get_newest_ready_snapshot",
    "get_or_create_worktree",
    "get_previous_ready_snapshot",
    "get_snapshot",
    "list_ready_snapshots",
    "mark_snapshot_failed",
    "mark_snapshot_ready",
]
