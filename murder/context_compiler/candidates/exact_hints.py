"""Exact path and symbol hint candidate provider (Part 9)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_FILE,
    CANDIDATE_KIND_SEMANTIC_UNIT,
    SCORE_AMBIGUOUS_PATH,
    SCORE_AMBIGUOUS_SYMBOL,
    SCORE_EXACT_PATH,
    SCORE_EXACT_QUALIFIED_SYMBOL,
    SCORE_EXACT_UNIQUE_SYMBOL,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.candidates.resolve import (
    snapshot_paths,
    top_level_units_for_path,
)
from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    search_units_by_name,
    search_units_by_semantic_role,
)
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry
from murder.context_compiler.persistence.files import get_file, normalize_relative_path
from murder.context_compiler.persistence.records import SemanticUnitVersionRecord
from murder.context_compiler.persistence.semantic_units import get_semantic_unit

PROVIDER_ID = "exact_hints"

# Common semantic-role vocabulary; only matched when the hint equals a role.
_KNOWN_SEMANTIC_ROLES = frozenset(
    {
        "component",
        "hook",
        "service",
        "handler",
        "entry_point",
        "test",
        "route",
        "controller",
        "model",
        "view",
        "store",
        "selector",
        "middleware",
        "schema",
        "config",
    }
)


@dataclass(frozen=True, slots=True)
class ExactHintsProvider:
    """Resolve explicit path/symbol hints against the current snapshot.

    Unique path-suffix matches are allowed. Ambiguous basename/suffix or
    unqualified-symbol matches are preserved (all returned) rather than
    silently collapsed to one.
    """

    conn: sqlite3.Connection
    include_top_level_units: bool = True
    max_candidates: int = 200

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        del prior_evidence
        out: list[Candidate] = []
        seen_ids: set[tuple[object, ...]] = set()

        for hint in request.path_hints:
            for candidate in self._path_hint_candidates(snapshot, hint):
                key = (
                    candidate.path,
                    candidate.unit_version_id,
                    candidate.candidate_kind,
                    candidate.reasons,
                )
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                out.append(candidate)
                if len(out) >= self.max_candidates:
                    return tuple(out)

        for hint in request.symbol_hints:
            for candidate in self._symbol_hint_candidates(snapshot, hint):
                key = (
                    candidate.path,
                    candidate.unit_version_id,
                    candidate.candidate_kind,
                    candidate.reasons,
                )
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                out.append(candidate)
                if len(out) >= self.max_candidates:
                    return tuple(out)

        return tuple(out)

    def _path_hint_candidates(self, snapshot: SnapshotRef, raw_hint: str) -> list[Candidate]:
        hint = raw_hint.strip().replace("\\", "/")
        if not hint:
            return []

        try:
            normalized = normalize_relative_path(hint)
        except ValueError:
            normalized = hint.lstrip("./")

        exact = get_file_version_by_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=normalized
        )
        if exact is not None:
            return self._file_hit_with_units(
                snapshot,
                path=exact.file.path,
                reason="exact_path_hint",
                score=SCORE_EXACT_PATH,
                metadata={"hint": raw_hint, "match": "exact"},
            )

        paths = snapshot_paths(self.conn, snapshot.snapshot_id)
        matches = [p for p in paths if p == normalized or p.endswith("/" + normalized)]
        matches = list(dict.fromkeys(matches))
        if not matches:
            return []
        if len(matches) == 1:
            return self._file_hit_with_units(
                snapshot,
                path=matches[0],
                reason="unique_path_suffix_hint",
                score=SCORE_EXACT_PATH,
                metadata={"hint": raw_hint, "match": "unique_suffix"},
            )

        results: list[Candidate] = []
        for path in matches:
            results.extend(
                self._file_hit_with_units(
                    snapshot,
                    path=path,
                    reason="ambiguous_path_hint",
                    score=SCORE_AMBIGUOUS_PATH,
                    metadata={
                        "hint": raw_hint,
                        "match": "ambiguous_suffix",
                        "match_count": len(matches),
                        "sibling_paths": tuple(matches),
                    },
                )
            )
        return results

    def _file_hit_with_units(
        self,
        snapshot: SnapshotRef,
        *,
        path: str,
        reason: str,
        score: float,
        metadata: dict[str, object],
    ) -> list[Candidate]:
        results = [
            Candidate(
                path=path,
                unit_id=None,
                unit_version_id=None,
                start_line=None,
                end_line=None,
                candidate_kind=CANDIDATE_KIND_FILE,
                reasons=(reason,),
                provider=PROVIDER_ID,
                raw_score=score,
                metadata=dict(metadata),
            )
        ]
        if not self.include_top_level_units:
            return results
        for unit in top_level_units_for_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=path
        ):
            results.append(
                Candidate(
                    path=path,
                    unit_id=unit.unit_id,
                    unit_version_id=unit.unit_version_id,
                    start_line=unit.start_line,
                    end_line=unit.end_line,
                    candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
                    reasons=(f"{reason}+top_level_unit",),
                    provider=PROVIDER_ID,
                    raw_score=score,
                    metadata={
                        **metadata,
                        "qualified_name": unit.qualified_name,
                        "language_kind": unit.language_kind,
                        "semantic_role": unit.semantic_role,
                    },
                )
            )
        return results

    def _symbol_hint_candidates(self, snapshot: SnapshotRef, raw_hint: str) -> list[Candidate]:
        hint = raw_hint.strip()
        if not hint:
            return []

        results: list[Candidate] = []

        if hint in _KNOWN_SEMANTIC_ROLES:
            role_hits = search_units_by_semantic_role(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                semantic_role=hint,
                limit=50,
            )
            for unit in role_hits:
                path = self._path_for_unit(unit)
                if path is None:
                    continue
                results.append(
                    self._unit_candidate(
                        path=path,
                        unit=unit,
                        reason="exact_semantic_role_hint",
                        score=(
                            SCORE_EXACT_UNIQUE_SYMBOL
                            if len(role_hits) == 1
                            else SCORE_AMBIGUOUS_SYMBOL
                        ),
                        metadata={
                            "hint": raw_hint,
                            "match": "semantic_role",
                            "match_count": len(role_hits),
                        },
                    )
                )

        qualified = search_units_by_name(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            name=hint,
            qualified=True,
            limit=50,
        )
        if qualified:
            for unit in qualified:
                path = self._path_for_unit(unit)
                if path is None:
                    continue
                results.append(
                    self._unit_candidate(
                        path=path,
                        unit=unit,
                        reason="exact_qualified_symbol_hint",
                        score=SCORE_EXACT_QUALIFIED_SYMBOL,
                        metadata={"hint": raw_hint, "match": "qualified"},
                    )
                )
            return results

        if (
            hint.startswith(".")
            or hint.startswith("#")
            or hint.startswith("[")
            or hint.startswith("@")
        ):
            selector_hits = self._selector_hits(snapshot, hint)
            results.extend(selector_hits)
            if selector_hits:
                return results

        unqualified = search_units_by_name(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            name=hint,
            qualified=False,
            limit=50,
        )
        if not unqualified:
            return results

        if len(unqualified) == 1:
            unit = unqualified[0]
            path = self._path_for_unit(unit)
            if path is None:
                return results
            results.append(
                self._unit_candidate(
                    path=path,
                    unit=unit,
                    reason="exact_unique_symbol_hint",
                    score=SCORE_EXACT_UNIQUE_SYMBOL,
                    metadata={"hint": raw_hint, "match": "unique_unqualified"},
                )
            )
            return results

        for unit in unqualified:
            path = self._path_for_unit(unit)
            if path is None:
                continue
            results.append(
                self._unit_candidate(
                    path=path,
                    unit=unit,
                    reason="ambiguous_symbol_hint",
                    score=SCORE_AMBIGUOUS_SYMBOL,
                    metadata={
                        "hint": raw_hint,
                        "match": "ambiguous_unqualified",
                        "match_count": len(unqualified),
                    },
                )
            )
        return results

    def _selector_hits(self, snapshot: SnapshotRef, hint: str) -> list[Candidate]:
        token = hint.lstrip(".#@[")
        token = token.rstrip("]")
        if not token:
            return []
        hits = search_units_by_name(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            name=token,
            qualified=None,
            limit=50,
        )
        role_hits = search_units_by_semantic_role(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            semantic_role="selector",
            limit=50,
        )
        combined = list(dict.fromkeys([*hits, *role_hits]))
        if not combined:
            return []
        score = SCORE_EXACT_UNIQUE_SYMBOL if len(combined) == 1 else SCORE_AMBIGUOUS_SYMBOL
        reason = (
            "exact_framework_selector_hint"
            if len(combined) == 1
            else "ambiguous_framework_selector_hint"
        )
        out: list[Candidate] = []
        for unit in combined:
            path = self._path_for_unit(unit)
            if path is None:
                continue
            out.append(
                self._unit_candidate(
                    path=path,
                    unit=unit,
                    reason=reason,
                    score=score,
                    metadata={
                        "hint": hint,
                        "match": "framework_selector",
                        "match_count": len(combined),
                    },
                )
            )
        return out

    def _path_for_unit(self, unit: SemanticUnitVersionRecord) -> str | None:
        logical = get_semantic_unit(self.conn, unit.unit_id)
        if logical is None:
            return None
        file_rec = get_file(self.conn, logical.file_id)
        return file_rec.path if file_rec is not None else None

    @staticmethod
    def _unit_candidate(
        *,
        path: str,
        unit: SemanticUnitVersionRecord,
        reason: str,
        score: float,
        metadata: dict[str, object],
    ) -> Candidate:
        return Candidate(
            path=path,
            unit_id=unit.unit_id,
            unit_version_id=unit.unit_version_id,
            start_line=unit.start_line,
            end_line=unit.end_line,
            candidate_kind=CANDIDATE_KIND_SEMANTIC_UNIT,
            reasons=(reason,),
            provider=PROVIDER_ID,
            raw_score=score,
            metadata={
                **metadata,
                "qualified_name": unit.qualified_name,
                "unqualified_name": unit.unqualified_name,
                "semantic_role": unit.semantic_role,
            },
        )


__all__ = ["ExactHintsProvider", "PROVIDER_ID"]
