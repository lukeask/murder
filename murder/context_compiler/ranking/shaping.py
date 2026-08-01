"""Shape ranked candidates into exact ``RangeProposal`` rows.

This is where judgement lives: units stay whole when they fit; lexical hits
get windows; file candidates never become whole files by default.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_DIFF_PATH,
    CANDIDATE_KIND_EXACT_RANGE,
    CANDIDATE_KIND_FILE,
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    CANDIDATE_KIND_TEST,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.candidates.resolve import top_level_units_for_path
from murder.context_compiler.candidates.tests import is_test_path
from murder.context_compiler.indexing.queries import (
    find_unit_containing_line,
    list_semantic_units_by_path,
)
from murder.context_compiler.models import EvidenceCategory, LineRange
from murder.context_compiler.persistence.semantic_units import get_semantic_unit_version
from murder.context_compiler.ranges import RangeValidationError, clamp_range, normalize_ranges
from murder.context_compiler.ranking.models import RangeProposal
from murder.context_compiler.ranking.policy import (
    CALL_SITE_CONTEXT_AFTER,
    CALL_SITE_CONTEXT_BEFORE,
    LEXICAL_CONTEXT_AFTER,
    LEXICAL_CONTEXT_BEFORE,
    LEXICAL_WINDOW_MERGE_GAP,
    LEXICAL_WINDOW_MERGE_MAX_LINES,
    SMALL_FILE_LINE_THRESHOLD,
    ProfileWeights,
)
from murder.context_compiler.ranking.scoring import ScoredCandidate
from murder.context_compiler.ranking.tokens import TokenCounter
from murder.context_compiler.ranking.trace import RankingTrace
from murder.context_compiler.rendering import RenderError, extract_source_slice
from murder.context_compiler.source import FilesystemSourceReader, SourceReadError


@dataclass(frozen=True, slots=True)
class _ShapedRange:
    path: str
    line_range: LineRange
    unit_version_id: int | None
    category: EvidenceCategory
    score: float
    reasons: tuple[str, ...]
    estimated_tokens: int


class RangeShaper:
    """Turn scored candidates into budgeted ``RangeProposal`` tuples."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        source_reader: FilesystemSourceReader,
        token_counter: TokenCounter,
    ) -> None:
        self._conn = conn
        self._reader = source_reader
        self._tokens = token_counter
        self._line_counts: dict[str, int] = {}
        self._texts: dict[str, str] = {}

    def shape(  # noqa: PLR0912
        self,
        scored: tuple[ScoredCandidate, ...],
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
        token_ceiling: int,
        trace: RankingTrace | None = None,
    ) -> tuple[tuple[RangeProposal, ...], int, bool]:
        """Return (ranges, total_tokens, truncated)."""
        per_file: dict[str, int] = {}
        accepted: list[RangeProposal] = []
        total_tokens = 0
        truncated = False

        # Collect lexical windows per path for nearby merge, then emit.
        pending_lexical: dict[str, list[_ShapedRange]] = {}

        for item in scored:
            if len(accepted) >= weights.max_range_proposals:
                truncated = True
                if trace is not None:
                    trace.record(
                        "excluded",
                        "max_range_proposals",
                        path=item.candidate.path,
                        score=item.score,
                    )
                break

            path = item.candidate.path
            if per_file.get(path, 0) >= weights.max_candidates_per_file:
                truncated = True
                if trace is not None:
                    trace.record(
                        "excluded",
                        "max_candidates_per_file",
                        path=path,
                        score=item.score,
                    )
                continue

            shaped_list = self._shape_one(item, snapshot=snapshot, weights=weights, trace=trace)
            for shaped in shaped_list:
                # Defer lexical-outside-unit windows for merge.
                if "shape:lexical_window" in shaped.reasons:
                    pending_lexical.setdefault(shaped.path, []).append(shaped)
                    continue

                if total_tokens + shaped.estimated_tokens > token_ceiling:
                    truncated = True
                    if trace is not None:
                        trace.record(
                            "excluded",
                            "token_ceiling",
                            path=shaped.path,
                            detail=str(shaped.estimated_tokens),
                            score=shaped.score,
                        )
                    continue

                if per_file.get(shaped.path, 0) >= weights.max_candidates_per_file:
                    truncated = True
                    continue
                if len(accepted) >= weights.max_range_proposals:
                    truncated = True
                    break

                accepted.append(
                    RangeProposal(
                        path=shaped.path,
                        line_range=shaped.line_range,
                        unit_version_id=shaped.unit_version_id,
                        category=shaped.category,
                        score=shaped.score,
                        reasons=shaped.reasons,
                        estimated_tokens=shaped.estimated_tokens,
                    )
                )
                per_file[shaped.path] = per_file.get(shaped.path, 0) + 1
                total_tokens += shaped.estimated_tokens

        # Merge and emit deferred lexical windows.
        for path, windows in sorted(pending_lexical.items()):
            for shaped in self._merge_lexical_windows(windows):
                if len(accepted) >= weights.max_range_proposals:
                    truncated = True
                    break
                if per_file.get(path, 0) >= weights.max_candidates_per_file:
                    truncated = True
                    continue
                if total_tokens + shaped.estimated_tokens > token_ceiling:
                    truncated = True
                    if trace is not None:
                        trace.record(
                            "excluded",
                            "token_ceiling",
                            path=path,
                            score=shaped.score,
                        )
                    continue
                accepted.append(
                    RangeProposal(
                        path=shaped.path,
                        line_range=shaped.line_range,
                        unit_version_id=shaped.unit_version_id,
                        category=shaped.category,
                        score=shaped.score,
                        reasons=shaped.reasons,
                        estimated_tokens=shaped.estimated_tokens,
                    )
                )
                per_file[path] = per_file.get(path, 0) + 1
                total_tokens += shaped.estimated_tokens

        return tuple(accepted), total_tokens, truncated

    def _shape_one(  # noqa: PLR0911, PLR0912
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
        trace: RankingTrace | None,
    ) -> list[_ShapedRange]:
        c = item.candidate
        kind = c.candidate_kind

        try:
            # Tests always prefer the focused test function over file/hint windows.
            if is_test_path(c.path):
                if c.unit_version_id is not None and c.start_line is not None:
                    return self._shape_test(item, weights=weights)
                if c.start_line is not None:
                    containing = find_unit_containing_line(
                        self._conn,
                        snapshot_id=snapshot.snapshot_id,
                        relative_path=c.path,
                        line=c.start_line,
                    )
                    if containing is not None:
                        refreshed = ScoredCandidate(
                            candidate=Candidate(
                                path=c.path,
                                unit_id=containing.unit_id,
                                unit_version_id=containing.unit_version_id,
                                start_line=containing.start_line,
                                end_line=containing.end_line,
                                candidate_kind=CANDIDATE_KIND_TEST,
                                reasons=c.reasons,
                                provider=c.provider,
                                raw_score=c.raw_score,
                                metadata=c.metadata,
                            ),
                            score=item.score,
                            reasons=item.reasons,
                            category=EvidenceCategory.TEST,
                            hop=item.hop,
                            estimated_tokens=item.estimated_tokens,
                        )
                        return self._shape_test(refreshed, weights=weights)
                # No line hit inside a unit — emit top-level test functions, never
                # the whole test module.
                tops = top_level_units_for_path(
                    self._conn, snapshot_id=snapshot.snapshot_id, relative_path=c.path
                )
                if tops:
                    out: list[_ShapedRange] = []
                    for unit in tops[: weights.max_candidates_per_file]:
                        refreshed = ScoredCandidate(
                            candidate=Candidate(
                                path=c.path,
                                unit_id=unit.unit_id,
                                unit_version_id=unit.unit_version_id,
                                start_line=unit.start_line,
                                end_line=unit.end_line,
                                candidate_kind=CANDIDATE_KIND_TEST,
                                reasons=c.reasons,
                                provider=c.provider,
                                raw_score=c.raw_score,
                                metadata=c.metadata,
                            ),
                            score=item.score,
                            reasons=item.reasons,
                            category=EvidenceCategory.TEST,
                            hop=item.hop,
                            estimated_tokens=item.estimated_tokens,
                        )
                        out.extend(self._shape_test(refreshed, weights=weights))
                    if out:
                        return out

            if kind == CANDIDATE_KIND_EXACT_RANGE or (
                c.provider == "exact_hints"
                and c.start_line is not None
                and c.end_line is not None
                and kind not in {CANDIDATE_KIND_SEMANTIC_UNIT, CANDIDATE_KIND_TEST}
            ):
                return self._shape_exact_hint(item)

            if kind == CANDIDATE_KIND_TEST:
                return self._shape_test(item, weights=weights)

            if kind == CANDIDATE_KIND_FILE or (
                kind == CANDIDATE_KIND_DIFF_PATH and c.start_line is None
            ):
                return self._shape_file(item, snapshot=snapshot, weights=weights, trace=trace)

            if kind == CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR:
                return self._shape_relationship(item, snapshot=snapshot, weights=weights)

            if kind in {CANDIDATE_KIND_SEMANTIC_UNIT, CANDIDATE_KIND_DIFF_PATH}:
                return self._shape_semantic(item, snapshot=snapshot, weights=weights)

            # Lexical exact ranges already handled; leftover ranges.
            if c.start_line is not None and c.end_line is not None:
                return self._shape_lexical_or_range(item, snapshot=snapshot, weights=weights)

            return self._shape_file(item, snapshot=snapshot, weights=weights, trace=trace)
        except (SourceReadError, RangeValidationError, RenderError, ValueError) as exc:
            if trace is not None:
                trace.record(
                    "excluded",
                    "shape_failed",
                    path=c.path,
                    detail=str(exc),
                    score=item.score,
                )
            return []

    def _shape_exact_hint(self, item: ScoredCandidate) -> list[_ShapedRange]:
        c = item.candidate
        assert c.start_line is not None and c.end_line is not None
        line_count = self._line_count(c.path)
        lr = clamp_range(LineRange(c.start_line, c.end_line), line_count)
        tokens = self._estimate(c.path, lr)
        return [
            _ShapedRange(
                path=c.path,
                line_range=lr,
                unit_version_id=c.unit_version_id,
                category=item.category,
                score=item.score,
                reasons=tuple(dict.fromkeys((*item.reasons, "shape:exact_hint"))),
                estimated_tokens=tokens,
            )
        ]

    def _shape_test(self, item: ScoredCandidate, *, weights: ProfileWeights) -> list[_ShapedRange]:
        """Focused test function only — never the enclosing file."""
        c = item.candidate
        if c.start_line is None or c.end_line is None:
            return []
        return self._emit_unit_or_focus(item, weights=weights, shape_tag="shape:focused_test")

    def _shape_semantic(
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
    ) -> list[_ShapedRange]:
        c = item.candidate
        # Lexical match inside a unit: whole unit if fits; else window.
        match_line = self._match_line(c)
        if (
            match_line is not None
            and c.unit_version_id is not None
            and c.start_line is not None
            and c.end_line is not None
        ):
            unit_tokens = self._estimate(c.path, LineRange(c.start_line, c.end_line))
            if unit_tokens <= weights.unit_token_cap:
                return self._emit_unit_or_focus(
                    item, weights=weights, shape_tag="shape:lexical_in_unit"
                )
            # Bounded window around match inside oversized unit.
            return [
                self._window_around(
                    item,
                    match_line,
                    before=LEXICAL_CONTEXT_BEFORE,
                    after=LEXICAL_CONTEXT_AFTER,
                    shape_tag="shape:oversized_unit_focus",
                    unit_version_id=c.unit_version_id,
                )
            ]

        if c.start_line is not None and c.end_line is not None:
            return self._emit_unit_or_focus(item, weights=weights, shape_tag="shape:semantic_unit")

        # Unit id without lines — resolve from snapshot.
        if c.unit_version_id is not None:
            units = list_semantic_units_by_path(
                self._conn, snapshot_id=snapshot.snapshot_id, relative_path=c.path
            )
            for unit in units:
                if unit.unit_version_id == c.unit_version_id:
                    refreshed = ScoredCandidate(
                        candidate=Candidate(
                            path=c.path,
                            unit_id=unit.unit_id,
                            unit_version_id=unit.unit_version_id,
                            start_line=unit.start_line,
                            end_line=unit.end_line,
                            candidate_kind=c.candidate_kind,
                            reasons=c.reasons,
                            provider=c.provider,
                            raw_score=c.raw_score,
                            metadata=c.metadata,
                        ),
                        score=item.score,
                        reasons=item.reasons,
                        category=item.category,
                        hop=item.hop,
                        estimated_tokens=item.estimated_tokens,
                    )
                    return self._emit_unit_or_focus(
                        refreshed, weights=weights, shape_tag="shape:semantic_unit"
                    )
        return []

    def _shape_lexical_or_range(
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
    ) -> list[_ShapedRange]:
        c = item.candidate
        assert c.start_line is not None and c.end_line is not None
        match_line = c.start_line
        containing = find_unit_containing_line(
            self._conn,
            snapshot_id=snapshot.snapshot_id,
            relative_path=c.path,
            line=match_line,
        )
        if containing is not None:
            unit_range = LineRange(containing.start_line, containing.end_line)
            unit_tokens = self._estimate(c.path, unit_range)
            if unit_tokens <= weights.unit_token_cap:
                refreshed = ScoredCandidate(
                    candidate=Candidate(
                        path=c.path,
                        unit_id=containing.unit_id,
                        unit_version_id=containing.unit_version_id,
                        start_line=containing.start_line,
                        end_line=containing.end_line,
                        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                        reasons=c.reasons,
                        provider=c.provider,
                        raw_score=c.raw_score,
                        metadata=c.metadata,
                    ),
                    score=item.score,
                    reasons=item.reasons,
                    category=item.category,
                    hop=item.hop,
                    estimated_tokens=item.estimated_tokens,
                )
                return self._emit_unit_or_focus(
                    refreshed, weights=weights, shape_tag="shape:lexical_in_unit"
                )
            return [
                self._window_around(
                    item,
                    match_line,
                    before=LEXICAL_CONTEXT_BEFORE,
                    after=LEXICAL_CONTEXT_AFTER,
                    shape_tag="shape:oversized_unit_focus",
                    unit_version_id=containing.unit_version_id,
                )
            ]

        # Outside any unit: 5 before / 10 after.
        return [
            self._window_around(
                item,
                match_line,
                before=LEXICAL_CONTEXT_BEFORE,
                after=LEXICAL_CONTEXT_AFTER,
                shape_tag="shape:lexical_window",
                unit_version_id=None,
            )
        ]

    def _shape_relationship(  # noqa: PLR0911
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
    ) -> list[_ShapedRange]:
        c = item.candidate
        rel_kind = str(c.metadata.get("relation_kind") or "")
        reasons_extra: list[str] = []

        owning = c.metadata.get("owning_component_path")
        if owning:
            reasons_extra.append(f"owning_component:{owning}")

        # Framework resources: relevant template/style region (file-level or unit).
        if rel_kind in {"template_of", "style_of"} or rel_kind.startswith("resource:"):
            if c.start_line is not None and c.end_line is not None:
                shaped = self._emit_unit_or_focus(
                    item,
                    weights=weights,
                    shape_tag="shape:framework_resource",
                    extra_reasons=tuple(reasons_extra),
                )
                return shaped
            # Whole small resource file when no unit span.
            line_count = self._line_count(c.path)
            if line_count <= SMALL_FILE_LINE_THRESHOLD:
                lr = LineRange(1, line_count)
                tokens = self._estimate(c.path, lr)
                return [
                    _ShapedRange(
                        path=c.path,
                        line_range=lr,
                        unit_version_id=c.unit_version_id,
                        category=item.category,
                        score=item.score,
                        reasons=tuple(
                            dict.fromkeys(
                                (
                                    *item.reasons,
                                    *reasons_extra,
                                    "shape:framework_resource",
                                )
                            )
                        ),
                        estimated_tokens=tokens,
                    )
                ]
            # Prefer top-level content of the resource file.
            tops = top_level_units_for_path(
                self._conn, snapshot_id=snapshot.snapshot_id, relative_path=c.path
            )
            if tops:
                unit = tops[0]
                refreshed = ScoredCandidate(
                    candidate=Candidate(
                        path=c.path,
                        unit_id=unit.unit_id,
                        unit_version_id=unit.unit_version_id,
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                        reasons=c.reasons,
                        provider=c.provider,
                        raw_score=c.raw_score,
                        metadata=c.metadata,
                    ),
                    score=item.score,
                    reasons=item.reasons,
                    category=item.category,
                    hop=item.hop,
                    estimated_tokens=item.estimated_tokens,
                )
                return self._emit_unit_or_focus(
                    refreshed,
                    weights=weights,
                    shape_tag="shape:framework_resource",
                    extra_reasons=tuple(reasons_extra),
                )
            # Fall back to a head window of the resource.
            end = min(line_count, LEXICAL_CONTEXT_BEFORE + LEXICAL_CONTEXT_AFTER + 1)
            lr = LineRange(1, max(1, end))
            tokens = self._estimate(c.path, lr)
            return [
                _ShapedRange(
                    path=c.path,
                    line_range=lr,
                    unit_version_id=None,
                    category=item.category,
                    score=item.score,
                    reasons=tuple(
                        dict.fromkeys((*item.reasons, *reasons_extra, "shape:framework_resource"))
                    ),
                    estimated_tokens=tokens,
                )
            ]

        # Call sites: containing unit when it fits, else relationship lines + window.
        if rel_kind == "calls" or "calls" in "".join(c.reasons):
            return self._shape_call_site(item, snapshot=snapshot, weights=weights)

        if c.start_line is not None and c.end_line is not None:
            return self._emit_unit_or_focus(
                item,
                weights=weights,
                shape_tag="shape:relationship_neighbor",
                extra_reasons=tuple(reasons_extra),
            )

        return self._shape_file(item, snapshot=snapshot, weights=weights, trace=None)

    def _shape_call_site(
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
    ) -> list[_ShapedRange]:
        c = item.candidate
        if c.start_line is not None and c.end_line is not None:
            tokens = self._estimate(c.path, LineRange(c.start_line, c.end_line))
            if tokens <= weights.unit_token_cap:
                return self._emit_unit_or_focus(
                    item, weights=weights, shape_tag="shape:call_site_unit"
                )

        rel_start = c.metadata.get("rel_start_line")
        rel_end = c.metadata.get("rel_end_line")
        anchor: int | None = None
        if isinstance(rel_start, int):
            anchor = rel_start
        elif c.start_line is not None:
            anchor = c.start_line
        if anchor is None:
            return []

        before = CALL_SITE_CONTEXT_BEFORE
        after = CALL_SITE_CONTEXT_AFTER
        if isinstance(rel_start, int) and isinstance(rel_end, int):
            # Window around the relationship span.
            line_count = self._line_count(c.path)
            lr = clamp_range(
                LineRange(max(1, rel_start - before), rel_end + after),
                line_count,
            )
            tokens = self._estimate(c.path, lr)
            return [
                _ShapedRange(
                    path=c.path,
                    line_range=lr,
                    unit_version_id=c.unit_version_id,
                    category=item.category,
                    score=item.score,
                    reasons=tuple(dict.fromkeys((*item.reasons, "shape:call_site_window"))),
                    estimated_tokens=tokens,
                )
            ]
        return [
            self._window_around(
                item,
                anchor,
                before=before,
                after=after,
                shape_tag="shape:call_site_window",
                unit_version_id=c.unit_version_id,
            )
        ]

    def _shape_file(
        self,
        item: ScoredCandidate,
        *,
        snapshot: SnapshotRef,
        weights: ProfileWeights,
        trace: RankingTrace | None,
    ) -> list[_ShapedRange]:
        """Never whole files by default."""
        c = item.candidate
        path = c.path
        line_count = self._line_count(path)

        # Prefer top matching / exported top-level units.
        tops = top_level_units_for_path(
            self._conn, snapshot_id=snapshot.snapshot_id, relative_path=path
        )
        exported = [u for u in tops if u.exported]
        preferred = exported or tops
        if preferred:
            out: list[_ShapedRange] = []
            for unit in preferred[: weights.max_candidates_per_file]:
                refreshed = ScoredCandidate(
                    candidate=Candidate(
                        path=path,
                        unit_id=unit.unit_id,
                        unit_version_id=unit.unit_version_id,
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                        reasons=c.reasons,
                        provider=c.provider,
                        raw_score=c.raw_score,
                        metadata={**dict(c.metadata), "exported": unit.exported},
                    ),
                    score=item.score,
                    reasons=item.reasons,
                    category=(EvidenceCategory.CONTRACT if unit.exported else item.category),
                    hop=item.hop,
                    estimated_tokens=item.estimated_tokens,
                )
                out.extend(
                    self._emit_unit_or_focus(
                        refreshed,
                        weights=weights,
                        shape_tag="shape:file_top_unit",
                    )
                )
            if out:
                return out

        # Whole-file only below small-file threshold when strongly relevant.
        strong = item.score >= weights.exact_hint * 0.8 or any(
            r.startswith("signal:exact_hint") for r in item.reasons
        )
        if line_count <= SMALL_FILE_LINE_THRESHOLD and strong:
            lr = LineRange(1, line_count)
            tokens = self._estimate(path, lr)
            return [
                _ShapedRange(
                    path=path,
                    line_range=lr,
                    unit_version_id=None,
                    category=item.category,
                    score=item.score,
                    reasons=tuple(dict.fromkeys((*item.reasons, "shape:small_file"))),
                    estimated_tokens=tokens,
                )
            ]

        if trace is not None:
            trace.record(
                "excluded",
                "file_not_shaped_to_whole",
                path=path,
                score=item.score,
                detail="no units; above small-file threshold or weak relevance",
            )
        # Last resort: head window, not whole file.
        end = min(line_count, LEXICAL_CONTEXT_BEFORE + LEXICAL_CONTEXT_AFTER + 1)
        if end < 1:
            return []
        lr = LineRange(1, end)
        tokens = self._estimate(path, lr)
        return [
            _ShapedRange(
                path=path,
                line_range=lr,
                unit_version_id=None,
                category=item.category,
                score=item.score,
                reasons=tuple(dict.fromkeys((*item.reasons, "shape:file_head_window"))),
                estimated_tokens=tokens,
            )
        ]

    def _emit_unit_or_focus(
        self,
        item: ScoredCandidate,
        *,
        weights: ProfileWeights,
        shape_tag: str,
        extra_reasons: tuple[str, ...] = (),
    ) -> list[_ShapedRange]:
        c = item.candidate
        assert c.start_line is not None and c.end_line is not None
        line_count = self._line_count(c.path)
        lr = clamp_range(LineRange(c.start_line, c.end_line), line_count)
        tokens = self._estimate(c.path, lr)
        reasons = list(dict.fromkeys((*item.reasons, *extra_reasons, shape_tag)))

        if tokens > weights.unit_token_cap:
            match_line = self._match_line(c)
            if match_line is not None:
                focused = self._window_around(
                    item,
                    match_line,
                    before=LEXICAL_CONTEXT_BEFORE,
                    after=LEXICAL_CONTEXT_AFTER,
                    shape_tag="shape:oversized_unit_focus",
                    unit_version_id=c.unit_version_id,
                    extra_reasons=(*extra_reasons, "oversized_unit"),
                )
                return [focused]
            # Keep oversized unit but mark it — representable without forcing
            # whole-unit inclusion when a focus exists; here we still emit the
            # unit with an oversized marker for Step 5.
            reasons.append("oversized_unit")
            reasons.append("shape:oversized_kept")

        return [
            _ShapedRange(
                path=c.path,
                line_range=lr,
                unit_version_id=c.unit_version_id,
                category=item.category,
                score=item.score,
                reasons=tuple(dict.fromkeys(reasons)),
                estimated_tokens=tokens,
            )
        ]

    def _window_around(
        self,
        item: ScoredCandidate,
        line: int,
        *,
        before: int,
        after: int,
        shape_tag: str,
        unit_version_id: int | None,
        extra_reasons: tuple[str, ...] = (),
    ) -> _ShapedRange:
        c = item.candidate
        line_count = self._line_count(c.path)
        lr = clamp_range(LineRange(max(1, line - before), line + after), line_count)
        tokens = self._estimate(c.path, lr)
        return _ShapedRange(
            path=c.path,
            line_range=lr,
            unit_version_id=unit_version_id,
            category=item.category,
            score=item.score,
            reasons=tuple(dict.fromkeys((*item.reasons, *extra_reasons, shape_tag))),
            estimated_tokens=tokens,
        )

    def _merge_lexical_windows(self, windows: list[_ShapedRange]) -> list[_ShapedRange]:
        if not windows:
            return []
        ordered = sorted(windows, key=lambda w: (w.line_range.start_line, -w.score))
        ranges = [w.line_range for w in ordered]
        # Merge nearby when result stays small.
        merged_ranges: list[LineRange] = []
        scores: list[float] = []
        reasons: list[tuple[str, ...]] = []
        cats: list[EvidenceCategory] = []
        paths: list[str] = []
        for window in ordered:
            if not merged_ranges:
                merged_ranges.append(window.line_range)
                scores.append(window.score)
                reasons.append(window.reasons)
                cats.append(window.category)
                paths.append(window.path)
                continue
            prev = merged_ranges[-1]
            gap = window.line_range.start_line - prev.end_line
            span = window.line_range.end_line - prev.start_line + 1
            if 0 < gap <= LEXICAL_WINDOW_MERGE_GAP and span <= LEXICAL_WINDOW_MERGE_MAX_LINES:
                merged_ranges[-1] = LineRange(prev.start_line, window.line_range.end_line)
                scores[-1] = max(scores[-1], window.score)
                reasons[-1] = tuple(
                    dict.fromkeys((*reasons[-1], *window.reasons, "shape:merged_windows"))
                )
            elif window.line_range.start_line <= prev.end_line + 1:
                merged = normalize_ranges((prev, window.line_range))
                merged_ranges[-1] = merged[0]
                scores[-1] = max(scores[-1], window.score)
                reasons[-1] = tuple(
                    dict.fromkeys((*reasons[-1], *window.reasons, "shape:merged_windows"))
                )
            else:
                merged_ranges.append(window.line_range)
                scores.append(window.score)
                reasons.append(window.reasons)
                cats.append(window.category)
                paths.append(window.path)
        _ = ranges
        out: list[_ShapedRange] = []
        for i, lr in enumerate(merged_ranges):
            path = paths[i] if i < len(paths) else ordered[0].path
            tokens = self._estimate(path, lr)
            out.append(
                _ShapedRange(
                    path=path,
                    line_range=lr,
                    unit_version_id=None,
                    category=cats[i] if i < len(cats) else ordered[0].category,
                    score=scores[i],
                    reasons=reasons[i],
                    estimated_tokens=tokens,
                )
            )
        return out

    def _match_line(self, candidate: Candidate) -> int | None:
        for key in ("match_line", "match_start_line", "hit_line", "rel_start_line"):
            value = candidate.metadata.get(key)
            if isinstance(value, int) and value > 0:
                return value
        if candidate.candidate_kind == CANDIDATE_KIND_EXACT_RANGE and candidate.start_line:
            return candidate.start_line
        return None

    def _line_count(self, path: str) -> int:
        if path not in self._line_counts:
            snap = self._reader.read(path)
            self._texts[path] = snap.text
            self._line_counts[path] = snap.line_count
        return self._line_counts[path]

    def _estimate(self, path: str, line_range: LineRange) -> int:
        if path not in self._texts:
            self._line_count(path)
        text = self._texts[path]
        try:
            slice_text = extract_source_slice(text, line_range.start_line, line_range.end_line)
        except RenderError:
            # Clamp and retry.
            lr = clamp_range(line_range, self._line_count(path))
            slice_text = extract_source_slice(text, lr.start_line, lr.end_line)
        return self._tokens.count_tokens(slice_text)


@dataclass(frozen=True, slots=True)
class CategoryShapeResult:
    """Outcome of category-driven range reshaping (Step 4 rules, Step 5 reuse)."""

    proposal: RangeProposal | None
    reject_reason: str | None = None
    reject_detail: str = ""
    repair_reason: str | None = None


def reshape_proposal_by_category(
    rng: RangeProposal,
    category: EvidenceCategory,
    *,
    conn: sqlite3.Connection | None,
    snapshot_id: int,
    source_reader: FilesystemSourceReader,
    token_counter: TokenCounter,
    unit_token_cap: int,
) -> CategoryShapeResult:
    """Shape an already-proposed range by evidence category.

    Category determines shape deterministically — the same rules Step 4 uses for
    tests / edit targets / contracts. ``supporting_context`` and similar keep the
    proposed span. Shared by Step 5 post-validation so grading does not diverge.
    """
    try:
        snap = source_reader.read(rng.path)
    except SourceReadError as exc:
        return CategoryShapeResult(
            proposal=None,
            reject_reason="source_unreadable",
            reject_detail=str(exc),
        )

    if category is EvidenceCategory.TEST or is_test_path(rng.path):
        unit_range = _containing_unit_for_proposal(rng, conn=conn, snapshot_id=snapshot_id)
        if unit_range is None:
            return CategoryShapeResult(
                proposal=_retag_proposal(rng, EvidenceCategory.TEST, "shape:grade_test_keep")
            )
        return _emit_category_unit(
            rng,
            unit_range,
            category=EvidenceCategory.TEST,
            tag="shape:grade_focused_test",
            source_reader=source_reader,
            token_counter=token_counter,
            unit_token_cap=unit_token_cap,
            line_count=snap.line_count,
        )

    if category in {EvidenceCategory.EDIT_TARGET, EvidenceCategory.CONTRACT}:
        unit_range = _containing_unit_for_proposal(rng, conn=conn, snapshot_id=snapshot_id)
        if unit_range is None:
            return CategoryShapeResult(
                proposal=_retag_proposal(rng, category, "shape:grade_keep_proposed")
            )
        return _emit_category_unit(
            rng,
            unit_range,
            category=category,
            tag="shape:grade_containing_unit",
            source_reader=source_reader,
            token_counter=token_counter,
            unit_token_cap=unit_token_cap,
            line_count=snap.line_count,
        )

    # supporting_context / verification / current_diff / other → proposed range
    return CategoryShapeResult(
        proposal=_retag_proposal(rng, category, "shape:grade_proposed_range")
    )


def _containing_unit_for_proposal(
    rng: RangeProposal,
    *,
    conn: sqlite3.Connection | None,
    snapshot_id: int,
) -> tuple[LineRange, int | None] | None:
    if conn is None:
        return None
    if rng.unit_version_id is not None:
        unit = get_semantic_unit_version(conn, rng.unit_version_id)
        if unit is not None:
            return LineRange(unit.start_line, unit.end_line), unit.unit_version_id
    unit = find_unit_containing_line(
        conn,
        snapshot_id=snapshot_id,
        relative_path=rng.path,
        line=rng.line_range.start_line,
    )
    if unit is None:
        return None
    return LineRange(unit.start_line, unit.end_line), unit.unit_version_id


def _emit_category_unit(
    rng: RangeProposal,
    unit_range: tuple[LineRange, int | None],
    *,
    category: EvidenceCategory,
    tag: str,
    source_reader: FilesystemSourceReader,
    token_counter: TokenCounter,
    unit_token_cap: int,
    line_count: int,
) -> CategoryShapeResult:
    lr, unit_version_id = unit_range
    try:
        lr = clamp_range(lr, line_count)
        snap = source_reader.read(rng.path)
        tokens = token_counter.count_tokens(
            extract_source_slice(snap.text, lr.start_line, lr.end_line)
        )
    except (SourceReadError, RangeValidationError, RenderError, ValueError) as exc:
        return CategoryShapeResult(
            proposal=None,
            reject_reason="shape_failed",
            reject_detail=str(exc),
        )

    reasons = tuple(dict.fromkeys((*rng.reasons, tag)))
    if tokens > unit_token_cap:
        try:
            proposed = clamp_range(rng.line_range, line_count)
            tokens = token_counter.count_tokens(
                extract_source_slice(snap.text, proposed.start_line, proposed.end_line)
            )
            reasons = tuple(dict.fromkeys((*reasons, "shape:grade_oversized_focus")))
            return CategoryShapeResult(
                proposal=RangeProposal(
                    path=rng.path,
                    line_range=proposed,
                    unit_version_id=unit_version_id,
                    category=category,
                    score=rng.score,
                    reasons=reasons,
                    estimated_tokens=tokens,
                ),
                repair_reason="oversized_unit_focus",
            )
        except (RangeValidationError, RenderError, ValueError):
            pass

    return CategoryShapeResult(
        proposal=RangeProposal(
            path=rng.path,
            line_range=lr,
            unit_version_id=unit_version_id,
            category=category,
            score=rng.score,
            reasons=reasons,
            estimated_tokens=tokens,
        )
    )


def _retag_proposal(
    rng: RangeProposal,
    category: EvidenceCategory,
    tag: str,
) -> RangeProposal:
    return RangeProposal(
        path=rng.path,
        line_range=rng.line_range,
        unit_version_id=rng.unit_version_id,
        category=category,
        score=rng.score,
        reasons=tuple(dict.fromkeys((*rng.reasons, tag))),
        estimated_tokens=rng.estimated_tokens,
    )


__all__ = [
    "CategoryShapeResult",
    "RangeShaper",
    "_ShapedRange",
    "reshape_proposal_by_category",
]
