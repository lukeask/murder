"""Step 6 — agent-local evidence ledger tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from murder.context_compiler import (
    DeletionNotice,
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceLedgerStatus,
    EvidenceScope,
    EvidenceSegment,
    FilesystemSourceReader,
    LedgerEntryDraft,
    LineRange,
    PayloadKind,
    RecipientProfile,
    SelectedRange,
    SqliteEvidenceLedger,
    assemble_exact_evidence,
    build_focused_diff,
    drafts_from_segments,
    hash_source_bytes,
    plan_evidence,
    render_deletion_notice,
    render_evidence_segment,
    render_source_segment,
)
from murder.context_compiler.ledger.policy import (
    FOCUSED_DIFF_TRUNCATION_MARKER,
    MAX_EVIDENCE_BLOB_CHARS,
    MAX_FOCUSED_DIFF_CHARS,
)
from murder.context_compiler.models import ContextRequest, RepositoryState
from murder.context_compiler.persistence import open_context_index
from murder.context_compiler.persistence.schema import SCHEMA_VERSION
from murder.context_compiler.ports import EvidenceLedger
from murder.context_compiler.rendering import RenderError, extract_source_slice

STATE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _scope(
    repo: Path,
    worktree: Path,
    *,
    session: str | None = "sess-1",
    conversation: str | None = None,
    recipient: str = "agent-1",
) -> EvidenceScope:
    return EvidenceScope(
        repository_root=repo,
        worktree_root=worktree,
        recipient_id=recipient,
        session_id=session,
        conversation_id=conversation,
    )


def _draft(
    path: str,
    start: int,
    end: int,
    source_hash: str,
    text: str,
    *,
    category: EvidenceCategory = EvidenceCategory.EDIT_TARGET,
) -> LedgerEntryDraft:
    return LedgerEntryDraft(
        path=path,
        start_line=start,
        end_line=end,
        source_hash=source_hash,
        text=text,
        category=category,
        payload_kind=PayloadKind.SOURCE,
    )


def _memory_entry(
    *,
    path: str,
    start: int,
    end: int,
    source_hash: str,
    repo: Path,
    worktree: Path,
    text: str | None = None,
    status: EvidenceLedgerStatus | None = None,
    supplied: bool = True,
    session_id: str | None = "sess-1",
) -> EvidenceLedgerEntry:
    return EvidenceLedgerEntry(
        recipient_id="agent-1",
        repository_root=repo,
        worktree_root=worktree,
        state_timestamp=STATE_TS,
        source_hash=source_hash,
        path=path,
        start_line=start,
        end_line=end,
        reason="prior",
        recipient_profile=RecipientProfile.IMPLEMENTATION,
        operation_id="op-0",
        session_id=session_id,
        supplied=supplied,
        status=status,
        payload_text=text,
        category=EvidenceCategory.EDIT_TARGET,
    )


def test_schema_version_bumped_for_step6() -> None:
    assert SCHEMA_VERSION == 3  # noqa: PLR2004


def test_prepared_does_not_subtract_supplied_does_abandoned_does_not(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "\n".join(f"L{i}" for i in range(1, 11)) + "\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    excerpt = extract_source_slice(text, 4, 7)
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    draft = _draft("mod.py", 4, 7, source_hash, excerpt)

    delivery = ledger.prepare_entries(scope, [draft])
    prepared_view = _memory_entry(
        path="mod.py",
        start=4,
        end=7,
        source_hash=source_hash,
        repo=repo,
        worktree=worktree,
        text=excerpt,
        status=EvidenceLedgerStatus.PREPARED,
        supplied=False,
    )
    selection = [
        SelectedRange(
            path="mod.py",
            line_range=LineRange(1, 10),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        )
    ]
    # Prepared must not subtract.
    assert ledger.load_supplied(scope) == ()
    planned = plan_evidence(selection, [prepared_view], reader)
    assert [(s.start_line, s.end_line) for s in planned.segments] == [(1, 10)]

    ledger.mark_supplied(delivery)
    supplied = list(ledger.load_supplied(scope))
    assert len(supplied) == 1
    planned = plan_evidence(selection, supplied, reader)
    assert [(s.start_line, s.end_line) for s in planned.segments] == [(1, 3), (8, 10)]

    # Abandoned delivery never becomes known.
    delivery2 = ledger.prepare_entries(
        scope,
        [_draft("mod.py", 1, 2, source_hash, extract_source_slice(text, 1, 2))],
    )
    ledger.mark_abandoned(delivery2)
    assert all(
        e.start_line != 1 or e.end_line != 2  # noqa: PLR2004
        for e in ledger.load_supplied(scope)
    )
    conn.close()


def test_other_session_and_worktree_do_not_subtract(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    text = "a\nb\nc\nd\ne\n"
    _write(wt_a / "mod.py", text)
    _write(wt_b / "mod.py", text)
    reader_a = FilesystemSourceReader(wt_a)
    source_hash = reader_a.read("mod.py").source_hash
    excerpt = extract_source_slice(text, 1, 5)

    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope_a = _scope(repo, wt_a, session="sess-a")
    scope_b = _scope(repo, wt_a, session="sess-b")
    scope_wt = _scope(repo, wt_b, session="sess-a")

    delivery = ledger.prepare_entries(scope_a, [_draft("mod.py", 1, 5, source_hash, excerpt)])
    ledger.mark_supplied(delivery)

    selection = [
        SelectedRange(
            path="mod.py",
            line_range=LineRange(1, 5),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        )
    ]
    # Same worktree, other session: no subtraction.
    assert plan_evidence(selection, list(ledger.load_supplied(scope_b)), reader_a).segments
    assert [
        (s.start_line, s.end_line)
        for s in plan_evidence(selection, list(ledger.load_supplied(scope_b)), reader_a).segments
    ] == [(1, 5)]

    # Other worktree scope is empty.
    assert ledger.load_supplied(scope_wt) == ()
    reader_b = FilesystemSourceReader(wt_b)
    assert [
        (s.start_line, s.end_line)
        for s in plan_evidence(selection, list(ledger.load_supplied(scope_wt)), reader_b).segments
    ] == [(1, 5)]

    # Own scope fully covered.
    own = plan_evidence(selection, list(ledger.load_supplied(scope_a)), reader_a)
    assert own.segments == ()
    conn.close()


def test_matching_hash_subtracts_overlap_full_coverage_emits_nothing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "\n".join(f"line-{i}" for i in range(1, 101)) + "\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    prior = _memory_entry(
        path="mod.py",
        start=54,
        end=90,
        source_hash=source_hash,
        repo=repo,
        worktree=worktree,
        text=extract_source_slice(text, 54, 90),
        status=EvidenceLedgerStatus.SUPPLIED,
    )
    planned = plan_evidence(
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(23, 97),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [prior],
        reader,
    )
    assert [(s.start_line, s.end_line) for s in planned.segments] == [(23, 53), (91, 97)]

    full = plan_evidence(
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(54, 90),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [prior],
        reader,
    )
    assert full.segments == ()


def test_changed_hash_focused_diff_and_deleted_range(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    old_text = "\n".join(f"old-{i}" for i in range(1, 21)) + "\n"
    _write(worktree / "mod.py", old_text)
    reader = FilesystemSourceReader(worktree)
    old_hash = reader.read("mod.py").source_hash
    prior_main = _memory_entry(
        path="mod.py",
        start=5,
        end=12,
        source_hash=old_hash,
        repo=repo,
        worktree=worktree,
        text=extract_source_slice(old_text, 5, 12),
        status=EvidenceLedgerStatus.SUPPLIED,
    )
    prior_deleted = _memory_entry(
        path="mod.py",
        start=18,
        end=20,
        source_hash=old_hash,
        repo=repo,
        worktree=worktree,
        text=extract_source_slice(old_text, 18, 20),
        status=EvidenceLedgerStatus.SUPPLIED,
        session_id="sess-1",
    )

    new_text = "\n".join(f"new-{i}" for i in range(1, 16)) + "\n"
    _write(worktree / "mod.py", new_text)
    reader = FilesystemSourceReader(worktree)

    planned = plan_evidence(
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(4, 10),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [prior_main, prior_deleted],
        reader,
    )
    assert len(planned.segments) == 1
    assert planned.segments[0].payload_kind is PayloadKind.DIFF
    assert "mod.py:5-12 -> mod.py:4-10" in planned.segments[0].payload_text
    assert len(planned.deletion_notices) == 1
    assert planned.deletion_notices[0].start_line == 18  # noqa: PLR2004
    assert planned.deletion_notices[0].end_line == 20  # noqa: PLR2004
    assert "entry_id" not in planned.deletion_notices[0].render()


def test_successful_delivery_updates_failed_leaves_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "one\ntwo\nthree\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)

    ok = ledger.prepare_entries(
        scope, [_draft("mod.py", 1, 2, source_hash, extract_source_slice(text, 1, 2))]
    )
    fail = ledger.prepare_entries(
        scope, [_draft("mod.py", 3, 3, source_hash, extract_source_slice(text, 3, 3))]
    )
    assert ledger.load_supplied(scope) == ()

    ledger.mark_supplied(ok)
    ledger.mark_abandoned(fail)
    supplied = ledger.load_supplied(scope)
    assert len(supplied) == 1
    assert supplied[0].start_line == 1 and supplied[0].end_line == 2  # noqa: PLR2004
    conn.close()


def test_blobs_deduplicate_by_content_hash(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    text = "same excerpt\nline two"
    digest = hash_source_bytes(text.encode("utf-8"))
    d1 = _draft("a.py", 1, 2, "hash-a", text)
    d2 = _draft("b.py", 10, 11, "hash-b", text)
    ledger.prepare_entries(scope, [d1])
    ledger.prepare_entries(scope, [d2])
    assert ledger.blob_count() == 1
    row = conn.execute("SELECT content_hash FROM evidence_blobs").fetchone()
    assert row is not None
    assert row["content_hash"] == digest
    conn.close()


def test_survives_process_restart_including_dirty_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    dirty = "dirty-line-1\ndirty-line-2\ndirty-line-3\n"
    _write(worktree / "mod.py", dirty)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    db_path = repo / "context-index.db"

    conn1 = open_context_index(repo, db_path=db_path)
    ledger1 = SqliteEvidenceLedger(conn1)
    scope = _scope(repo, worktree)
    excerpt = extract_source_slice(dirty, 1, 3)
    delivery = ledger1.prepare_entries(scope, [_draft("mod.py", 1, 3, source_hash, excerpt)])
    ledger1.mark_supplied(delivery)
    conn1.close()

    # Restart: new connection; worktree still dirty / changed.
    changed = "changed-1\nchanged-2\nchanged-3\nchanged-4\n"
    _write(worktree / "mod.py", changed)
    conn2 = open_context_index(repo, db_path=db_path)
    ledger2 = SqliteEvidenceLedger(conn2)
    prior = list(ledger2.load_supplied(scope))
    assert len(prior) == 1
    assert prior[0].payload_text == excerpt
    assert prior[0].source_hash == source_hash

    reader2 = FilesystemSourceReader(worktree)
    planned = plan_evidence(
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(1, 4),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        prior,
        reader2,
    )
    assert planned.segments[0].payload_kind is PayloadKind.DIFF
    assert prior[0].payload_text is not None
    assert "dirty-line-1" in planned.segments[0].payload_text
    conn2.close()


def test_diff_output_deterministic_and_obeys_size_bound() -> None:
    old = "\n".join(f"old-{i}" for i in range(100))
    new = "\n".join(f"new-{i}" for i in range(100))
    first = build_focused_diff(
        path="src/foo.py",
        old_range=LineRange(54, 90),
        old_text="\n".join(old.splitlines()[53:90]),
        new_range=LineRange(61, 102),
        new_text="\n".join(new.splitlines()[60:102]),
    )
    second = build_focused_diff(
        path="src/foo.py",
        old_range=LineRange(54, 90),
        old_text="\n".join(old.splitlines()[53:90]),
        new_range=LineRange(61, 102),
        new_text="\n".join(new.splitlines()[60:102]),
    )
    assert first.text == second.text
    assert first.text.startswith("src/foo.py:54-90 -> src/foo.py:61-102\n")
    assert not first.truncated

    huge_old = "\n".join(f"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa{i}" for i in range(2000))
    huge_new = "\n".join(f"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb{i}" for i in range(2000))
    bounded = build_focused_diff(
        path="big.py",
        old_range=LineRange(1, 2000),
        old_text=huge_old,
        new_range=LineRange(1, 2000),
        new_text=huge_new,
        max_chars=500,
    )
    assert bounded.truncated
    assert FOCUSED_DIFF_TRUNCATION_MARKER in bounded.text
    assert len(bounded.text) <= 500 + len(FOCUSED_DIFF_TRUNCATION_MARKER)
    assert len(bounded.text) <= MAX_FOCUSED_DIFF_CHARS or bounded.truncated


def test_assemble_exact_evidence_ignores_prepared_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "a\nb\nc\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    request = ContextRequest(
        request_id="r1",
        recipient_id="agent-1",
        repository_state=RepositoryState(
            repository_root=repo,
            worktree_root=worktree,
            state_timestamp=STATE_TS,
        ),
        objective="t",
        recipient_profile=RecipientProfile.IMPLEMENTATION,
        session_id="sess-1",
    )
    prepared = _memory_entry(
        path="mod.py",
        start=1,
        end=3,
        source_hash=source_hash,
        repo=repo,
        worktree=worktree,
        status=EvidenceLedgerStatus.PREPARED,
        supplied=False,
    )
    result = assemble_exact_evidence(
        request,
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(1, 3),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [prepared],
        reader,
    )
    assert [(s.start_line, s.end_line) for s in result.segments] == [(1, 3)]


def test_ledger_lifetime_independent_of_snapshot_gc(tmp_path: Path) -> None:
    """Blobs survive when no indexing snapshots exist; cleanup is explicit."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    text = "keep me"
    delivery = ledger.prepare_entries(scope, [_draft("a.py", 1, 1, "h", text)])
    ledger.mark_supplied(delivery)
    assert ledger.blob_count() == 1
    # No snapshots — apply_retention would be a no-op; ledger still present.
    assert len(ledger.load_supplied(scope)) == 1
    ledger.cleanup_session("sess-1")
    assert ledger.load_supplied(scope) == ()
    assert ledger.blob_count() == 0
    conn.close()


def test_file_deletion_emits_deletion_notice(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "gone\nsoon\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    prior = _memory_entry(
        path="mod.py",
        start=1,
        end=2,
        source_hash=source_hash,
        repo=repo,
        worktree=worktree,
        text=text.strip("\n"),
        status=EvidenceLedgerStatus.SUPPLIED,
    )
    (worktree / "mod.py").unlink()
    planned = plan_evidence([], [prior], reader)
    assert len(planned.deletion_notices) == 1
    assert planned.deletion_notices[0].path == "mod.py"


def test_cleanup_scope_gc_orphaned_blobs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger: EvidenceLedger = SqliteEvidenceLedger(conn)
    scope_keep = _scope(repo, worktree, session="keep")
    scope_drop = _scope(repo, worktree, session="drop")
    shared = "shared-blob-text"
    unique_drop = "only-in-drop"
    d_keep = ledger.prepare_entries(scope_keep, [_draft("a.py", 1, 1, "h1", shared)])
    d_drop = ledger.prepare_entries(
        scope_drop,
        [
            _draft("a.py", 1, 1, "h1", shared),
            _draft("b.py", 1, 1, "h2", unique_drop),
        ],
    )
    ledger.mark_supplied(d_keep)
    ledger.mark_supplied(d_drop)
    assert ledger.blob_count() == 2  # noqa: PLR2004

    deleted = ledger.cleanup_scope(scope_drop)
    assert deleted == 2  # noqa: PLR2004
    assert ledger.load_supplied(scope_drop) == ()
    assert len(ledger.load_supplied(scope_keep)) == 1
    # Shared blob retained; unique-to-drop blob GC'd.
    assert ledger.blob_count() == 1
    conn.close()


def test_cleanup_abandoned_deliveries_gc_blobs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    ok = ledger.prepare_entries(scope, [_draft("a.py", 1, 1, "h", "kept")])
    abandoned = ledger.prepare_entries(scope, [_draft("b.py", 1, 1, "h", "gone-blob")])
    ledger.mark_supplied(ok)
    ledger.mark_abandoned(abandoned)
    assert ledger.blob_count() == 2  # noqa: PLR2004

    deleted = ledger.cleanup_abandoned_deliveries()
    assert deleted == 1
    assert ledger.blob_count() == 1
    assert len(ledger.load_supplied(scope)) == 1
    assert ledger.load_supplied(scope)[0].payload_text == "kept"
    conn.close()


def test_cleanup_repository_removes_all_scopes_and_blobs(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir()
    wt_b.mkdir()
    # Both scopes share one context-index under repo_a for the test DB.
    conn = open_context_index(repo_a, db_path=repo_a / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope_a = _scope(repo_a, wt_a, session="s-a")
    scope_b = EvidenceScope(
        repository_root=repo_b,
        worktree_root=wt_b,
        recipient_id="agent-1",
        session_id="s-b",
    )
    d_a = ledger.prepare_entries(scope_a, [_draft("a.py", 1, 1, "ha", "text-a")])
    d_b = ledger.prepare_entries(scope_b, [_draft("b.py", 1, 1, "hb", "text-b")])
    ledger.mark_supplied(d_a)
    ledger.mark_supplied(d_b)
    assert ledger.blob_count() == 2  # noqa: PLR2004

    deleted = ledger.cleanup_repository(repo_a)
    assert deleted == 1
    assert ledger.load_supplied(scope_a) == ()
    assert len(ledger.load_supplied(scope_b)) == 1
    assert ledger.blob_count() == 1
    conn.close()


def test_conversation_and_recipient_isolation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "a\nb\nc\nd\ne\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    excerpt = extract_source_slice(text, 1, 5)

    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    # Conversation-keyed scopes (no session_id).
    scope_conv_a = _scope(repo, worktree, session=None, conversation="conv-a")
    scope_conv_b = _scope(repo, worktree, session=None, conversation="conv-b")
    scope_other_recipient = _scope(
        repo, worktree, session=None, conversation="conv-a", recipient="agent-2"
    )

    delivery = ledger.prepare_entries(scope_conv_a, [_draft("mod.py", 1, 5, source_hash, excerpt)])
    ledger.mark_supplied(delivery)

    selection = [
        SelectedRange(
            path="mod.py",
            line_range=LineRange(1, 5),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        )
    ]
    # Other conversation: no subtraction.
    other_conv = plan_evidence(selection, list(ledger.load_supplied(scope_conv_b)), reader)
    assert [(s.start_line, s.end_line) for s in other_conv.segments] == [(1, 5)]

    # Other recipient, same conversation id: no subtraction.
    other_recip = plan_evidence(
        selection, list(ledger.load_supplied(scope_other_recipient)), reader
    )
    assert [(s.start_line, s.end_line) for s in other_recip.segments] == [(1, 5)]

    # Own conversation fully covered.
    own = plan_evidence(selection, list(ledger.load_supplied(scope_conv_a)), reader)
    assert own.segments == ()
    conn.close()


def test_session_keyed_recipient_id_isolation(tmp_path: Path) -> None:
    """Session-scoped ledgers isolate by recipient_id (not only conversation)."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "a\nb\nc\nd\ne\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    excerpt = extract_source_slice(text, 1, 5)

    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope_agent1 = _scope(repo, worktree, session="sess-shared", recipient="agent-1")
    scope_agent2 = _scope(repo, worktree, session="sess-shared", recipient="agent-2")

    delivery = ledger.prepare_entries(
        scope_agent1, [_draft("mod.py", 1, 5, source_hash, excerpt)]
    )
    ledger.mark_supplied(delivery)

    selection = [
        SelectedRange(
            path="mod.py",
            line_range=LineRange(1, 5),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        )
    ]
    # Same session id, different recipient: no subtraction.
    other = plan_evidence(selection, list(ledger.load_supplied(scope_agent2)), reader)
    assert [(s.start_line, s.end_line) for s in other.segments] == [(1, 5)]
    assert ledger.load_supplied(scope_agent2) == ()

    own = plan_evidence(selection, list(ledger.load_supplied(scope_agent1)), reader)
    assert own.segments == ()
    conn.close()


def test_build_focused_diff_unmappable_when_both_excerpts_empty() -> None:
    result = build_focused_diff(
        path="gone.py",
        old_range=LineRange(1, 1),
        old_text="",
        new_range=LineRange(1, 1),
        new_text="",
    )
    assert result.unmappable is True
    assert result.text == ""
    assert result.truncated is False


def test_prepare_entries_rejects_oversized_blob(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    worktree.mkdir()
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    oversized = "x" * (MAX_EVIDENCE_BLOB_CHARS + 1)
    with pytest.raises(ValueError, match="MAX_EVIDENCE_BLOB_CHARS"):
        ledger.prepare_entries(scope, [_draft("big.py", 1, 1, "h", oversized)])
    assert ledger.blob_count() == 0
    assert ledger.load_supplied(scope) == ()
    conn.close()


def test_changed_evidence_unmappable_missing_prior_payload(tmp_path: Path) -> None:
    """Missing prior payload_text → current source + changed_evidence_unmappable."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    old_text = "old-1\nold-2\nold-3\n"
    _write(worktree / "mod.py", old_text)
    reader = FilesystemSourceReader(worktree)
    old_hash = reader.read("mod.py").source_hash
    prior = _memory_entry(
        path="mod.py",
        start=1,
        end=3,
        source_hash=old_hash,
        repo=repo,
        worktree=worktree,
        text=None,  # unmappable: no stored excerpt
        status=EvidenceLedgerStatus.SUPPLIED,
    )

    new_text = "new-1\nnew-2\nnew-3\nnew-4\n"
    _write(worktree / "mod.py", new_text)
    reader = FilesystemSourceReader(worktree)
    new_hash = reader.read("mod.py").source_hash

    planned = plan_evidence(
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(1, 4),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [prior],
        reader,
    )
    assert len(planned.segments) == 1
    assert planned.segments[0].payload_kind is PayloadKind.SOURCE
    assert planned.segments[0].payload_text == extract_source_slice(new_text, 1, 4)
    assert planned.segments[0].source_hash == new_hash
    assert "mod.py" in planned.unmappable_paths
    assert any(
        n.message == "changed_evidence_unmappable" and n.path == "mod.py"
        for n in planned.changed_notices
    )


def test_end_to_end_round_trip_subtract_and_diff(tmp_path: Path) -> None:
    """plan → drafts → prepare → mark_supplied → reload → second plan."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text_v1 = "\n".join(f"v1-{i}" for i in range(1, 11)) + "\n"
    _write(worktree / "mod.py", text_v1)
    reader = FilesystemSourceReader(worktree)
    hash_v1 = reader.read("mod.py").source_hash
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)

    selection_full = [
        SelectedRange(
            path="mod.py",
            line_range=LineRange(1, 10),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        )
    ]
    first = plan_evidence(selection_full, list(ledger.load_supplied(scope)), reader)
    assert len(first.segments) == 1
    assert first.segments[0].payload_kind is PayloadKind.SOURCE

    drafts = drafts_from_segments(first.segments)
    delivery = ledger.prepare_entries(scope, drafts)
    ledger.mark_supplied(delivery)

    # Second plan against same source: full coverage → nothing.
    reloaded = list(ledger.load_supplied(scope))
    assert len(reloaded) == 1
    assert reloaded[0].delivery_id == delivery
    assert reloaded[0].operation_id == ""  # Step-0 field not invented from delivery_id
    assert reloaded[0].later_opened is False
    second = plan_evidence(selection_full, reloaded, reader)
    assert second.segments == ()

    # Change source: focused diff on next plan.
    text_v2 = "\n".join(f"v2-{i}" for i in range(1, 11)) + "\n"
    _write(worktree / "mod.py", text_v2)
    reader2 = FilesystemSourceReader(worktree)
    third = plan_evidence(selection_full, list(ledger.load_supplied(scope)), reader2)
    assert len(third.segments) == 1
    assert third.segments[0].payload_kind is PayloadKind.DIFF
    assert "mod.py:1-10 -> mod.py:1-10" in third.segments[0].payload_text
    assert hash_v1 != reader2.read("mod.py").source_hash

    # Diff is recipient-renderable without internal IDs.
    rendered = render_evidence_segment(third.segments[0])
    assert rendered.endswith("\n")
    assert "entry_id" not in rendered
    assert "delivery_id" not in rendered
    assert delivery not in rendered

    # E2E: DIFF payload itself survives prepare → mark_supplied → reload.
    diff_drafts = drafts_from_segments(third.segments)
    assert diff_drafts[0].payload_kind is PayloadKind.DIFF
    assert diff_drafts[0].text == third.segments[0].payload_text
    diff_delivery = ledger.prepare_entries(scope, diff_drafts)
    ledger.mark_supplied(diff_delivery)
    reloaded_diff = [
        e for e in ledger.load_supplied(scope) if e.delivery_id == diff_delivery
    ]
    assert len(reloaded_diff) == 1
    assert reloaded_diff[0].payload_kind is PayloadKind.DIFF
    assert reloaded_diff[0].payload_text == third.segments[0].payload_text
    assert reloaded_diff[0].status is EvidenceLedgerStatus.SUPPLIED
    conn.close()


def test_end_to_end_deletion_notice_path_through_ledger(tmp_path: Path) -> None:
    """Supplied SOURCE → file gone → deletion notice; ledger still holds prior."""
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "keep\nme\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    conn = open_context_index(repo, db_path=repo / "context-index.db")
    ledger = SqliteEvidenceLedger(conn)
    scope = _scope(repo, worktree)
    excerpt = extract_source_slice(text, 1, 2)
    delivery = ledger.prepare_entries(
        scope, [_draft("mod.py", 1, 2, source_hash, excerpt)]
    )
    ledger.mark_supplied(delivery)
    prior = list(ledger.load_supplied(scope))
    assert len(prior) == 1

    (worktree / "mod.py").unlink()
    planned = plan_evidence([], prior, FilesystemSourceReader(worktree))
    assert len(planned.deletion_notices) == 1
    notice = planned.deletion_notices[0]
    assert notice.path == "mod.py"
    assert notice.start_line == 1 and notice.end_line == 2  # noqa: PLR2004
    rendered = render_deletion_notice(notice)
    assert "mod.py" in rendered
    assert "entry_id" not in rendered
    assert delivery not in rendered
    # Prior knowledge remains supplied until explicit cleanup.
    assert len(ledger.load_supplied(scope)) == 1
    conn.close()


def test_recipient_rendering_diff_and_deletion() -> None:
    source = EvidenceSegment(
        path="a.py",
        start_line=1,
        end_line=1,
        source_hash="h",
        payload_kind=PayloadKind.SOURCE,
        payload_text="x = 1",
        symbol_ids=(),
        category=EvidenceCategory.EDIT_TARGET,
        reason="r",
    )
    assert render_evidence_segment(source) == render_source_segment(source)

    diff_text = "a.py:1-2 -> a.py:1-2\n@@ -1,2 +1,2 @@\n-old\n+new\n"
    diff_seg = EvidenceSegment(
        path="a.py",
        start_line=1,
        end_line=2,
        source_hash="h2",
        payload_kind=PayloadKind.DIFF,
        payload_text=diff_text,
        symbol_ids=(),
        category=EvidenceCategory.EDIT_TARGET,
        reason="r",
    )
    with pytest.raises(RenderError, match="source renderer"):
        render_source_segment(diff_seg)
    rendered_diff = render_evidence_segment(diff_seg)
    assert rendered_diff == diff_text
    assert "entry_id" not in rendered_diff

    notice = DeletionNotice(path="gone.py", start_line=3, end_line=5, source_hash="hx")
    rendered_del = render_deletion_notice(notice)
    assert rendered_del == "deleted: gone.py:3-5\n"
    assert "entry_id" not in rendered_del
    assert "source_hash" not in rendered_del
