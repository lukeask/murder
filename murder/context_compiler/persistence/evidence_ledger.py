"""SQLite persistence for the agent-local evidence ledger (Step 6).

Tables live in ``.murder/context-index.db``. Lifetime follows the session —
independent of two-snapshot index retention. Blobs outlive snapshots; GC a
blob when no entry references it.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from murder.context_compiler.ledger.policy import DELIVERY_ID_PREFIX, MAX_EVIDENCE_BLOB_CHARS
from murder.context_compiler.models import (
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceLedgerStatus,
    EvidenceScope,
    LedgerEntryDraft,
    PayloadKind,
)
from murder.context_compiler.persistence.connection import transaction
from murder.context_compiler.source import count_source_lines, hash_source_bytes


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    # Accept both ``Z`` and offset forms written by this module.
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _norm_nullable(value: str | None) -> str:
    """Normalize NULL-able scope identity fields for UNIQUE matching."""
    return value if value is not None else ""


class SqliteEvidenceLedger:
    """``EvidenceLedger`` backed by the experimental context-index DB."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def prepare_entries(
        self,
        scope: EvidenceScope,
        drafts: Sequence[LedgerEntryDraft],
    ) -> str:
        """Persist prepared entries for ``scope``; return a new ``delivery_id``.

        Does **not** mark anything supplied — the consumer must deliver first.
        """
        if not drafts:
            raise ValueError("prepare_entries requires at least one draft")
        delivery_id = f"{DELIVERY_ID_PREFIX}{uuid.uuid4().hex}"
        prepared_at = _now()
        with transaction(self._conn):
            scope_id = self._get_or_create_scope(scope, seen_at=prepared_at)
            for draft in drafts:
                if len(draft.text) > MAX_EVIDENCE_BLOB_CHARS:
                    raise ValueError(
                        f"evidence blob for {draft.path}:{draft.start_line}-"
                        f"{draft.end_line} exceeds MAX_EVIDENCE_BLOB_CHARS "
                        f"({MAX_EVIDENCE_BLOB_CHARS})"
                    )
                content_hash = hash_source_bytes(draft.text.encode("utf-8"))
                self._upsert_blob(content_hash, draft.text, prepared_at)
                self._conn.execute(
                    """
                    INSERT INTO evidence_ledger_entries (
                        scope_id, delivery_id, path, start_line, end_line,
                        source_hash, content_hash, category, payload_kind,
                        status, prepared_at, supplied_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, NULL)
                    """,
                    (
                        scope_id,
                        delivery_id,
                        draft.path,
                        draft.start_line,
                        draft.end_line,
                        draft.source_hash,
                        content_hash,
                        draft.category.value,
                        draft.payload_kind.value,
                        prepared_at,
                    ),
                )
        return delivery_id

    def mark_supplied(self, delivery_id: str) -> None:
        """Confirm the recipient received ``delivery_id``.

        Only ``prepared`` rows flip to ``supplied``. Calling on an unknown or
        already-finalized delivery is a no-op for foreign rows and a no-op for
        already-supplied ones.
        """
        supplied_at = _now()
        with transaction(self._conn):
            self._conn.execute(
                """
                UPDATE evidence_ledger_entries
                   SET status = 'supplied', supplied_at = ?
                 WHERE delivery_id = ? AND status = 'prepared'
                """,
                (supplied_at, delivery_id),
            )

    def mark_abandoned(self, delivery_id: str) -> None:
        """Abandon a delivery that never reached the recipient."""
        with transaction(self._conn):
            self._conn.execute(
                """
                UPDATE evidence_ledger_entries
                   SET status = 'abandoned'
                 WHERE delivery_id = ? AND status = 'prepared'
                """,
                (delivery_id,),
            )
            self._gc_orphaned_blobs()

    def load_supplied(self, scope: EvidenceScope) -> Sequence[EvidenceLedgerEntry]:
        """Load confirmed-supplied entries for ``scope``, including blob text."""
        scope_id = self._find_scope_id(scope)
        if scope_id is None:
            return ()
        self._touch_scope(scope_id)
        rows = self._conn.execute(
            """
            SELECT e.entry_id, e.delivery_id, e.path, e.start_line, e.end_line,
                   e.source_hash, e.content_hash, e.category, e.payload_kind,
                   e.status, e.prepared_at, e.supplied_at, b.text AS payload_text,
                   s.repository_root, s.worktree_root, s.recipient_id,
                   s.session_id, s.conversation_id
              FROM evidence_ledger_entries AS e
              JOIN evidence_blobs AS b ON b.content_hash = e.content_hash
              JOIN evidence_scopes AS s ON s.scope_id = e.scope_id
             WHERE e.scope_id = ? AND e.status = 'supplied'
             ORDER BY e.path ASC, e.start_line ASC, e.entry_id ASC
            """,
            (scope_id,),
        ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def cleanup_scope(self, scope: EvidenceScope) -> int:
        """Delete all ledger rows for ``scope``; GC orphaned blobs. Returns rows deleted."""
        scope_id = self._find_scope_id(scope)
        if scope_id is None:
            return 0
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM evidence_ledger_entries WHERE scope_id = ?",
                (scope_id,),
            )
            deleted = int(cur.rowcount)
            self._conn.execute(
                "DELETE FROM evidence_scopes WHERE scope_id = ?",
                (scope_id,),
            )
            self._gc_orphaned_blobs()
        return deleted

    def cleanup_session(self, session_id: str) -> int:
        """Delete every scope tied to ``session_id``."""
        with transaction(self._conn):
            scope_ids = [
                int(row["scope_id"])
                for row in self._conn.execute(
                    "SELECT scope_id FROM evidence_scopes WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
            ]
            deleted = 0
            for scope_id in scope_ids:
                cur = self._conn.execute(
                    "DELETE FROM evidence_ledger_entries WHERE scope_id = ?",
                    (scope_id,),
                )
                deleted += int(cur.rowcount)
                self._conn.execute(
                    "DELETE FROM evidence_scopes WHERE scope_id = ?",
                    (scope_id,),
                )
            self._gc_orphaned_blobs()
        return deleted

    def cleanup_abandoned_deliveries(self) -> int:
        """Remove abandoned entries and GC blobs. Returns entries deleted."""
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM evidence_ledger_entries WHERE status = 'abandoned'"
            )
            deleted = int(cur.rowcount)
            self._gc_orphaned_blobs()
        return deleted

    def cleanup_repository(self, repository_root: Path) -> int:
        """Remove all scopes for ``repository_root``."""
        root = str(repository_root)
        with transaction(self._conn):
            scope_ids = [
                int(row["scope_id"])
                for row in self._conn.execute(
                    "SELECT scope_id FROM evidence_scopes WHERE repository_root = ?",
                    (root,),
                ).fetchall()
            ]
            deleted = 0
            for scope_id in scope_ids:
                cur = self._conn.execute(
                    "DELETE FROM evidence_ledger_entries WHERE scope_id = ?",
                    (scope_id,),
                )
                deleted += int(cur.rowcount)
                self._conn.execute(
                    "DELETE FROM evidence_scopes WHERE scope_id = ?",
                    (scope_id,),
                )
            self._gc_orphaned_blobs()
        return deleted

    def blob_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM evidence_blobs").fetchone()
        assert row is not None
        return int(row["n"])

    def _get_or_create_scope(self, scope: EvidenceScope, *, seen_at: str) -> int:
        existing = self._find_scope_id(scope)
        if existing is not None:
            self._conn.execute(
                "UPDATE evidence_scopes SET last_seen_at = ? WHERE scope_id = ?",
                (seen_at, existing),
            )
            return existing
        # Store empty string instead of NULL so UNIQUE (…, session_id,
        # conversation_id) treats missing identity fields as one scope.
        cur = self._conn.execute(
            """
            INSERT INTO evidence_scopes (
                repository_root, worktree_root, recipient_id,
                session_id, conversation_id, created_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(scope.repository_root),
                str(scope.worktree_root),
                scope.recipient_id,
                _norm_nullable(scope.session_id),
                _norm_nullable(scope.conversation_id),
                seen_at,
                seen_at,
            ),
        )
        row_id = cur.lastrowid
        if row_id is None:
            raise RuntimeError("failed to allocate evidence_scopes.scope_id")
        return int(row_id)

    def _find_scope_id(self, scope: EvidenceScope) -> int | None:
        row = self._conn.execute(
            """
            SELECT scope_id FROM evidence_scopes
             WHERE repository_root = ?
               AND worktree_root = ?
               AND recipient_id = ?
               AND IFNULL(session_id, '') = ?
               AND IFNULL(conversation_id, '') = ?
            """,
            (
                str(scope.repository_root),
                str(scope.worktree_root),
                scope.recipient_id,
                _norm_nullable(scope.session_id),
                _norm_nullable(scope.conversation_id),
            ),
        ).fetchone()
        if row is None:
            return None
        return int(row["scope_id"])

    def _touch_scope(self, scope_id: int) -> None:
        self._conn.execute(
            "UPDATE evidence_scopes SET last_seen_at = ? WHERE scope_id = ?",
            (_now(), scope_id),
        )

    def _upsert_blob(self, content_hash: str, text: str, created_at: str) -> None:
        self._conn.execute(
            """
            INSERT INTO evidence_blobs (content_hash, text, line_count, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(content_hash) DO NOTHING
            """,
            (content_hash, text, count_source_lines(text), created_at),
        )

    def _gc_orphaned_blobs(self) -> int:
        cur = self._conn.execute(
            """
            DELETE FROM evidence_blobs
             WHERE content_hash NOT IN (
                 SELECT DISTINCT content_hash FROM evidence_ledger_entries
             )
            """
        )
        return int(cur.rowcount)

    def _row_to_entry(self, row: sqlite3.Row) -> EvidenceLedgerEntry:
        status = EvidenceLedgerStatus(str(row["status"]))
        category = EvidenceCategory(str(row["category"]))
        payload_kind = PayloadKind(str(row["payload_kind"]))
        prepared_at = _parse_ts(str(row["prepared_at"]))
        supplied_at = _parse_ts(row["supplied_at"])
        session_raw = row["session_id"]
        conversation_raw = row["conversation_id"]
        session_id = str(session_raw) if session_raw else None
        conversation_id = str(conversation_raw) if conversation_raw else None
        # Step-0-only fields (reason / recipient_profile / operation_id /
        # later_*) stay at defaults — they are not ledger columns.
        return EvidenceLedgerEntry(
            recipient_id=str(row["recipient_id"]),
            repository_root=Path(str(row["repository_root"])),
            worktree_root=Path(str(row["worktree_root"])),
            state_timestamp=supplied_at or prepared_at or datetime.now(timezone.utc),
            source_hash=str(row["source_hash"]),
            path=str(row["path"]),
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            session_id=session_id,
            conversation_id=conversation_id,
            supplied=status is EvidenceLedgerStatus.SUPPLIED,
            content_hash=str(row["content_hash"]),
            category=category,
            payload_kind=payload_kind,
            status=status,
            delivery_id=str(row["delivery_id"]),
            entry_id=int(row["entry_id"]),
            prepared_at=prepared_at,
            supplied_at=supplied_at,
            payload_text=str(row["payload_text"]),
        )


__all__ = [
    "SqliteEvidenceLedger",
]
