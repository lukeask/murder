"""One-time merger for pre-consolidation per-repository databases."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from murder.state.persistence.backup import backup_database, default_backup_path
from murder.state.persistence.connection import Connection, write_repository_id
from murder.state.storage.paths import db_path, legacy_db_path, repository_id_path
from murder.state.storage.service_registry import list_service_sessions

INTEGER_PK_TABLES = {
    "checklist": "id",
    "retained_facts": "sequence",
    "projection_inputs": "sequence",
    "escalations": "id",
    "plan_revisions": "id",
    "note_revisions": "id",
    "report_revisions": "id",
    "notes_entries": "id",
    "conversation_blocks": "id",
    "conversation_chunk_summaries": "summary_id",
    "harness_usage_snapshots": "id",
    "harness_control_semantic_events": "id",
    "harness_control_decisions": "id",
    "schedule_queue": "id",
}

# Parent-before-child order. Missing feature tables are skipped, allowing old
# databases from any release in the migration chain to be imported.
COPY_ORDER = (
    "runs",
    "tickets",
    "ticket_deps",
    "checklist",
    "check_results",
    "completion_attempts",
    "agents",
    "structured_decisions",
    "retained_facts",
    "projection_inputs",
    "commands",
    "worker_heartbeats",
    "escalations",
    "plans",
    "plan_revisions",
    "plan_related_tickets",
    "notes",
    "note_revisions",
    "reports",
    "report_revisions",
    "notetaker_context",
    "notes_entries",
    "agent_messages",
    "conversations",
    "conversation_blocks",
    "conversation_chunk_summaries",
    "chunk_summary_blocks",
    "harness_usage_snapshots",
    "harness_control_frames",
    "harness_control_evidence",
    "harness_control_observations",
    "harness_control_semantic_events",
    "harness_control_operations",
    "harness_control_actions",
    "harness_control_effects",
    "harness_control_decisions",
    "harness_usage_probe_sessions",
    "schedule_queue",
    "scheduler_state",
    "scheduler_params",
    "scheduler_steering",
    "scheduler_decision_cache",
    "harness_models",
    "map_summaries",
    "history_status",
    "workflow_runs",
    "workflow_state_migrations",
    "workflow_signals",
    "workflow_waits",
    "activities",
    "activity_reservations",
    "activity_reservation_locks",
    "activity_results",
    "workflow_triggers",
    "trigger_firings",
    "trigger_cursors",
    "trigger_manual_pending",
    "workflow_transition_outbox",
    "harness_sessions",
    "session_writer_fences",
    "writer_leases",
    "writer_lease_audit_facts",
    "permission_policy_decisions",
    "permission_approval_evidence",
    "permission_approval_requests",
    "permission_authorization_grants",
    "permission_authorization_uses",
    "permission_grant_revocations",
    "permission_safety_reviews",
)


def _columns(conn: Any, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _tables(conn: Any) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _repository_id(repo_root: Path) -> str:
    try:
        return str(UUID(repository_id_path(repo_root).read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return str(uuid5(NAMESPACE_URL, f"murder:repository:{repo_root.resolve()}"))


def _rename_legacy_files(path: Path) -> None:
    migrated = path.with_name(path.name + ".migrated")
    if migrated.exists():
        raise FileExistsError(migrated)
    path.replace(migrated)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            sidecar.replace(Path(str(migrated) + suffix))


def _service_is_live(pid: int) -> bool:
    """Conservatively determine whether a service-registry owner is live."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # We cannot safely conclude that a service owned by another user is
        # gone, so leave its database untouched.
        return True
    return True


def _copy_table(  # noqa: PLR0912, PLR0915
    target: Connection,
    source: sqlite3.Connection,
    table: str,
    repository_id: str,
    id_maps: dict[str, dict[int, int]],
) -> None:
    source_columns = _columns(source, table)
    target_info = target.execute(f'PRAGMA table_info("{table}")').fetchall()
    target_columns = [str(row[1]) for row in target_info]
    common = [column for column in target_columns if column in source_columns]
    source_has_repository = "repository_id" in source_columns
    if "repository_id" not in target_columns:
        raise RuntimeError(f"target table is not partitioned: {table}")

    select_sql = f'SELECT * FROM "{table}"'
    parameters: tuple[Any, ...] = ()
    if source_has_repository:
        select_sql += " WHERE repository_id = ?"
        parameters = (repository_id,)
    rows = source.execute(select_sql, parameters).fetchall()
    if not rows:
        return

    integer_pk = INTEGER_PK_TABLES.get(table)
    if integer_pk and integer_pk in common:
        current = target.execute(
            f'SELECT COALESCE(MAX("{integer_pk}"), 0) FROM "{table}"'
        ).fetchone()[0]
        mapping = {
            int(row[integer_pk]): int(current) + offset for offset, row in enumerate(rows, start=1)
        }
        id_maps[table] = mapping

    compatibility_columns: list[str] = []
    if table == "retained_facts":
        compatibility_columns = [
            column
            for column in (
                "schema_version",
                "occurred_at",
                "recorded_at",
                "actor_kind",
                "actor_id",
                "correlation_id",
                "payload_json",
            )
            if column in target_columns and column not in source_columns
        ]
    insert_columns = [
        "repository_id",
        *[c for c in common if c != "repository_id"],
        *compatibility_columns,
    ]
    if table in {"notetaker_context", "scheduler_state"}:
        insert_columns = [c for c in insert_columns if c != "id"]
    placeholders = ", ".join("?" for _ in insert_columns)
    quoted = ", ".join(f'"{column}"' for column in insert_columns)
    insert_sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'

    values: list[tuple[Any, ...]] = []
    for row in rows:
        if table == "chunk_summary_blocks":
            summary_id = id_maps.get("conversation_chunk_summaries", {}).get(int(row["summary_id"]))
            block_id = id_maps.get("conversation_blocks", {}).get(int(row["block_id"]))
            if summary_id is None or block_id is None:
                # block_id deliberately lacks an FK. Older databases can
                # therefore retain an attribution pointer after its block was
                # removed; preserve the summary and skip only that pointer.
                continue
        item: list[Any] = []
        for column in insert_columns:
            if column == "repository_id":
                item.append(repository_id)
            elif integer_pk == column:
                item.append(id_maps[table][int(row[column])])
            elif table == "chunk_summary_blocks" and column == "summary_id":
                item.append(summary_id)
            elif table == "chunk_summary_blocks" and column == "block_id":
                # This pointer is intentionally not declared as an FK, but it
                # still names conversation_blocks.id and must follow its remap.
                item.append(block_id)
            elif table == "escalations" and column == "source_event_id":
                item.append(None)
            elif table == "retained_facts" and column == "schema_version":
                item.append(1)
            elif table == "retained_facts" and column in {"occurred_at", "recorded_at"}:
                item.append("1970-01-01T00:00:00+00:00")
            elif table == "retained_facts" and column == "actor_kind":
                item.append("service")
            elif table == "retained_facts" and column == "actor_id":
                item.append("legacy-import")
            elif table == "retained_facts" and column == "correlation_id":
                item.append(str(row["fact_id"]))
            elif table == "retained_facts" and column == "payload_json":
                item.append("{}")
            else:
                item.append(row[column])
        values.append(tuple(item))
    target.executemany(insert_sql, values)


def merge_legacy_database(target: Connection, repo_root: Path) -> bool:
    """Merge one legacy DB, returning False when already imported or absent."""
    legacy = legacy_db_path(repo_root)
    if not legacy.exists():
        return False
    repository_id = _repository_id(repo_root)
    existing = target.execute(
        "SELECT 1 FROM repositories WHERE repository_id = ?", (repository_id,)
    ).fetchone()
    if existing is not None:
        # The registry row is the sole idempotency marker. A crash can occur
        # after its transaction commits but before the recoverable source rename.
        migrated = legacy.with_name(legacy.name + ".migrated")
        if not migrated.exists():
            _rename_legacy_files(legacy)
        return False

    # Preserve both the shared destination and the exact source before mutating
    # either. The source backup also checkpoints any live WAL consistently.
    shared = db_path()
    if shared.exists() and shared.stat().st_size:
        backup_database(shared, default_backup_path(label="pre-merge-shared"))
    backup_database(legacy, default_backup_path(label=f"pre-merge-{repository_id}"))

    source = sqlite3.connect(str(legacy))
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA foreign_keys = ON")
    source_tables = _tables(source)
    target_tables = _tables(target)
    id_maps: dict[str, dict[int, int]] = {}
    try:
        target.execute("BEGIN IMMEDIATE")
        for table in COPY_ORDER:
            if table in source_tables and table in target_tables:
                _copy_table(target, source, table, repository_id, id_maps)
        now = target.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        target.execute(
            "INSERT INTO repositories "
            "(repository_id, root_path, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (repository_id, str(repo_root.resolve()), now, now),
        )
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()

    write_repository_id(repository_id_path(repo_root), repository_id)
    _rename_legacy_files(legacy)
    return True


def merge_known_legacy_databases(
    target: Connection,
    current_repo: Path,
    explicit_repos: Iterable[Path] = (),
) -> list[Path]:
    current_root = current_repo.resolve()
    sessions = list_service_sessions()
    roots = {
        current_root,
        *(path.resolve() for path in explicit_repos),
        *(session.repo_root.resolve() for session in sessions),
    }
    # A live service may still be using its checkout-local pre-consolidation
    # database. Never copy or rename another live service's database as a side
    # effect of opening this repository. Stale registry entries remain eligible
    # for the one-time migration; the current repository remains eligible too.
    live_other_roots = {
        session.repo_root.resolve()
        for session in sessions
        if session.repo_root.resolve() != current_root and _service_is_live(session.pid)
    }
    roots.difference_update(live_other_roots)
    merged: list[Path] = []
    for root in sorted(roots, key=str):
        if merge_legacy_database(target, root):
            merged.append(root)
    return merged
