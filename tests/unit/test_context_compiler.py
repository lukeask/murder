"""Unit tests for the context compiler exact-evidence kernel (Step 0)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from murder.context_compiler import (
    ContextRequest,
    EvidenceCategory,
    EvidenceLedgerEntry,
    EvidenceSegment,
    FilesystemSourceReader,
    LineRange,
    PayloadKind,
    RangeValidationError,
    RecipientProfile,
    RepositoryState,
    SelectedRange,
    SourceReadError,
    assemble_exact_evidence,
    build_brief_from_selections,
    clamp_range,
    hash_source_bytes,
    normalize_ranges,
    render_source_segment,
    subtract_ranges,
)

STATE_TS = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
GEN_TS = datetime(2026, 8, 1, 8, 5, tzinfo=timezone.utc)


def _repo_state(repo: Path, worktree: Path) -> RepositoryState:
    return RepositoryState(
        repository_root=repo,
        worktree_root=worktree,
        state_timestamp=STATE_TS,
        commit_sha="abc123",
    )


def _request(repo: Path, worktree: Path) -> ContextRequest:
    return ContextRequest(
        request_id="req-1",
        recipient_id="agent-1",
        repository_state=_repo_state(repo, worktree),
        objective="implement exact evidence kernel",
        recipient_profile=RecipientProfile.IMPLEMENTATION,
        agent_id="agent-1",
        session_id="sess-1",
        trace_id="trace-1",
    )


def _ledger_entry(
    *,
    path: str,
    start: int,
    end: int,
    source_hash: str,
    repo: Path,
    worktree: Path,
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
        operation_id="turn-0",
        agent_id="agent-1",
        session_id="sess-1",
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- LineRange / range ops -------------------------------------------------


def test_line_range_rejects_zero_negative_and_inverted() -> None:
    with pytest.raises(ValueError, match="positive"):
        LineRange(0, 1)
    with pytest.raises(ValueError, match="positive"):
        LineRange(1, 0)
    with pytest.raises(ValueError, match="positive"):
        LineRange(-1, 3)
    with pytest.raises(ValueError, match="must be >="):
        LineRange(10, 9)


def test_normalize_merges_overlaps() -> None:
    result = normalize_ranges(
        [LineRange(23, 40), LineRange(35, 55), LineRange(56, 60), LineRange(80, 90)]
    )
    assert result == (LineRange(23, 60), LineRange(80, 90))


def test_normalize_merges_adjacent_ranges() -> None:
    result = normalize_ranges([LineRange(1, 5), LineRange(6, 10), LineRange(20, 21)])
    assert result == (LineRange(1, 10), LineRange(20, 21))


def test_subtract_exact_interval() -> None:
    result = subtract_ranges([LineRange(23, 97)], [LineRange(54, 90)])
    assert result == (LineRange(23, 53), LineRange(91, 97))


def test_subtract_full_coverage_yields_empty() -> None:
    result = subtract_ranges([LineRange(10, 20)], [LineRange(5, 25)])
    assert result == ()


def test_subtract_several_disjoint_known_ranges() -> None:
    result = subtract_ranges(
        [LineRange(1, 100)],
        [LineRange(10, 20), LineRange(40, 50), LineRange(90, 95)],
    )
    assert result == (
        LineRange(1, 9),
        LineRange(21, 39),
        LineRange(51, 89),
        LineRange(96, 100),
    )


def test_subtract_nested_and_crossing() -> None:
    result = subtract_ranges(
        [LineRange(1, 50), LineRange(60, 80)],
        [LineRange(5, 10), LineRange(8, 15), LineRange(70, 75)],
    )
    assert result == (
        LineRange(1, 4),
        LineRange(16, 50),
        LineRange(60, 69),
        LineRange(76, 80),
    )


def test_clamp_partial_overlong_and_reject_wholly_invalid() -> None:
    assert clamp_range(LineRange(8, 20), 10) == LineRange(8, 10)
    with pytest.raises(RangeValidationError, match="beyond"):
        clamp_range(LineRange(11, 15), 10)
    with pytest.raises(RangeValidationError, match="empty"):
        clamp_range(LineRange(1, 1), 0)


# --- Source hashing / reader -----------------------------------------------


def test_stable_source_hashing(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    target = worktree / "src" / "a.py"
    content = b"alpha\nbeta\n"
    _write(target, content.decode())
    reader = FilesystemSourceReader(worktree)
    first = reader.read("src/a.py")
    second = reader.read("src/a.py")
    assert first.source_hash == second.source_hash == hash_source_bytes(content)
    assert first.line_count == 2  # noqa: PLR2004


def test_worktree_root_reads_when_repository_root_differs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    _write(repo / "src" / "a.py", "from-repo\n")
    _write(worktree / "src" / "a.py", "from-worktree\n")
    reader = FilesystemSourceReader(worktree)
    snap = reader.read("src/a.py")
    assert snap.text == "from-worktree\n"
    assert "from-repo" not in snap.text


def test_rejects_absolute_paths_and_traversal(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope\n", encoding="utf-8")
    reader = FilesystemSourceReader(worktree)

    with pytest.raises(SourceReadError, match="absolute"):
        reader.read(str(outside))
    with pytest.raises(SourceReadError, match="traversal"):
        reader.read("../secret.txt")

    # Symlink escape
    (worktree / "link.py").symlink_to(outside)
    with pytest.raises(SourceReadError, match="escapes"):
        reader.read("link.py")


# --- Rendering -------------------------------------------------------------


def test_blank_line_numbering_and_whitespace_preservation() -> None:
    segment = EvidenceSegment(
        path="src/example.py",
        start_line=23,
        end_line=27,
        source_hash="h",
        payload_kind=PayloadKind.SOURCE,
        payload_text=(
            "def example(...):\n"
            "    value = prepare(...)\n"
            "\n"
            "    return fallback()\n"
            "    return commit(value)"
        ),
        symbol_ids=("example",),
        category=EvidenceCategory.EDIT_TARGET,
        reason="target",
    )
    rendered = render_source_segment(segment)
    assert rendered == (
        "src/example.py:23-27\n"
        "23 def example(...):\n"
        "24     value = prepare(...)\n"
        "25 \n"
        "26     return fallback()\n"
        "27     return commit(value)\n"
    )


def test_deterministic_rendering_trailing_newline() -> None:
    segment = EvidenceSegment(
        path="a.py",
        start_line=1,
        end_line=1,
        source_hash="h",
        payload_kind=PayloadKind.SOURCE,
        payload_text="x = 1",
        symbol_ids=(),
        category=EvidenceCategory.OTHER,
        reason="r",
    )
    assert render_source_segment(segment) == "a.py:1-1\n1 x = 1\n"
    assert render_source_segment(segment) == render_source_segment(segment)


# --- Assembly --------------------------------------------------------------


def test_unchanged_prior_evidence_subtraction(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    text = "\n".join(f"line-{i}" for i in range(1, 101)) + "\n"
    _write(worktree / "mod.py", text)
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash

    result = assemble_exact_evidence(
        _request(repo, worktree),
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(23, 97),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
                symbol_ids=("mod",),
            )
        ],
        [
            _ledger_entry(
                path="mod.py",
                start=54,
                end=90,
                source_hash=source_hash,
                repo=repo,
                worktree=worktree,
            )
        ],
        reader,
    )
    assert [(s.start_line, s.end_line) for s in result.segments] == [(23, 53), (91, 97)]
    assert result.changed_notices == ()


def test_changed_source_hash_classification(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _write(worktree / "mod.py", "\n".join(f"L{i}" for i in range(1, 21)) + "\n")
    reader = FilesystemSourceReader(worktree)

    result = assemble_exact_evidence(
        _request(repo, worktree),
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(5, 15),
                category=EvidenceCategory.SUPPORTING_CONTEXT,
                reason="support",
            )
        ],
        [
            _ledger_entry(
                path="mod.py",
                start=8,
                end=12,
                source_hash="stale-hash",
                repo=repo,
                worktree=worktree,
            )
        ],
        reader,
    )
    assert [(segment.start_line, segment.end_line) for segment in result.segments] == [
        (5, 7),
        (13, 15),
    ]
    assert len(result.changed_notices) == 1
    notice = result.changed_notices[0]
    assert notice.prior_hash == "stale-hash"
    assert notice.current_hash == result.segments[0].source_hash
    assert notice.message == "changed evidence requires refresh/diff"
    assert notice.overlapping_range == LineRange(8, 12)


def test_prior_evidence_is_scoped_to_request_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _write(worktree / "mod.py", "a\nb\nc\n")
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash
    unrelated = _ledger_entry(
        path="mod.py",
        start=1,
        end=3,
        source_hash=source_hash,
        repo=repo,
        worktree=worktree,
    )
    unrelated = replace(unrelated, recipient_id="another-agent")

    result = assemble_exact_evidence(
        _request(repo, worktree),
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(1, 3),
                category=EvidenceCategory.EDIT_TARGET,
                reason="edit",
            )
        ],
        [unrelated],
        reader,
    )
    assert [(segment.start_line, segment.end_line) for segment in result.segments] == [(1, 3)]


def test_fully_known_evidence_produces_no_segment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _write(worktree / "mod.py", "a\nb\nc\nd\ne\n")
    reader = FilesystemSourceReader(worktree)
    source_hash = reader.read("mod.py").source_hash

    result = assemble_exact_evidence(
        _request(repo, worktree),
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(2, 4),
                category=EvidenceCategory.TEST,
                reason="already known",
            )
        ],
        [
            _ledger_entry(
                path="mod.py",
                start=1,
                end=5,
                source_hash=source_hash,
                repo=repo,
                worktree=worktree,
            )
        ],
        reader,
    )
    assert result.segments == ()
    assert result.changed_notices == ()


def test_stable_output_ordering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _write(worktree / "a.py", "1\n2\n3\n4\n5\n")
    _write(worktree / "b.py", "1\n2\n3\n4\n5\n")
    reader = FilesystemSourceReader(worktree)

    selections = [
        SelectedRange(
            path="b.py",
            line_range=LineRange(4, 5),
            category=EvidenceCategory.OTHER,
            reason="other",
        ),
        SelectedRange(
            path="a.py",
            line_range=LineRange(3, 3),
            category=EvidenceCategory.TEST,
            reason="test",
        ),
        SelectedRange(
            path="a.py",
            line_range=LineRange(1, 2),
            category=EvidenceCategory.EDIT_TARGET,
            reason="edit",
        ),
        SelectedRange(
            path="b.py",
            line_range=LineRange(1, 1),
            category=EvidenceCategory.CONTRACT,
            reason="contract",
        ),
    ]
    result = assemble_exact_evidence(_request(repo, worktree), selections, (), reader)
    ordered = [(s.category, s.path, s.start_line) for s in result.segments]
    assert ordered == [
        (EvidenceCategory.EDIT_TARGET, "a.py", 1),
        (EvidenceCategory.CONTRACT, "b.py", 1),
        (EvidenceCategory.TEST, "a.py", 3),
        (EvidenceCategory.OTHER, "b.py", 4),
    ]


def test_profile_and_state_timestamp_preserved_through_assembly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "wt"
    _write(worktree / "mod.py", "hello\n")
    reader = FilesystemSourceReader(worktree)
    request = ContextRequest(
        request_id="req-preserve",
        recipient_id="planner-1",
        repository_state=_repo_state(repo, worktree),
        objective="plan",
        recipient_profile=RecipientProfile.PLANNING,
        trace_id="trace-preserve",
    )
    brief = build_brief_from_selections(
        request,
        [
            SelectedRange(
                path="mod.py",
                line_range=LineRange(1, 1),
                category=EvidenceCategory.CONTRACT,
                reason="api",
            )
        ],
        (),
        reader,
        generated_at=GEN_TS,
    )
    assert brief.recipient_profile is RecipientProfile.PLANNING
    assert brief.state_timestamp == STATE_TS
    assert brief.generated_timestamp == GEN_TS
    assert brief.trace_id == "trace-preserve"
    assert len(brief.evidence_segments) == 1
