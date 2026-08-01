"""One-/two-hop expansion through snapshot-scoped relationships.

Expansion yields candidates that compete on score like any other. Edges whose
only basis is a filename heuristic are rejected.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    SCORE_DIRECT_STRUCTURAL,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.candidates.resolve import (
    path_for_file_id,
    path_for_file_version_id,
    unit_and_path_in_snapshot,
)
from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    list_incoming_relationships,
    list_outgoing_relationships,
)
from murder.context_compiler.indexing.resolution_policy import CONFIDENCE_WEAK, normalize_confidence
from murder.context_compiler.models import RecipientProfile
from murder.context_compiler.persistence.records import RelationshipRecord
from murder.context_compiler.ranking.policy import (
    EXPAND_RELATION_KINDS,
    FILENAME_HEURISTIC_METHODS,
    PLANNING_EXTRA_INCOMING,
    SECOND_HOP_MIN,
    ProfileWeights,
)
from murder.context_compiler.ranking.scoring import (
    ScoredCandidate,
    merge_candidate_maps,
    ranking_identity,
    score_candidate,
)

PROVIDER_ID = "ranking_expansion"


def _is_filename_only(rel: RelationshipRecord) -> bool:
    method = (rel.resolution_method or "").strip()
    if method in FILENAME_HEURISTIC_METHODS:
        return True
    if "filename" in method:
        return True
    try:
        tier = normalize_confidence(rel.confidence)
    except ValueError:
        return False
    return tier == CONFIDENCE_WEAK and "filename" in method


def _kind_matches_preferred(kind: str, preferred: frozenset[str]) -> bool:
    """True when ``kind`` is in ``preferred``, including resource:* aliases."""
    if kind in preferred:
        return True
    if kind.startswith("resource:"):
        # resource:template ↔ template_of; resource:style ↔ style_of
        suffix = kind.split(":", 1)[1]
        if suffix == "template" and "template_of" in preferred:
            return True
        if suffix == "style" and "style_of" in preferred:
            return True
    return False


def _relation_allowed(  # noqa: PLR0911
    rel: RelationshipRecord,
    *,
    profile: RecipientProfile,
    direction: str,
    preferred_kinds: frozenset[str] = frozenset(),
) -> bool:
    if _is_filename_only(rel):
        return False
    kind = rel.relation_kind
    # Gap-driven expansion: when the request names kinds, filter to those.
    if preferred_kinds:
        if kind.startswith("resource:") and _kind_matches_preferred(kind, preferred_kinds):
            return True
        return _kind_matches_preferred(kind, preferred_kinds)
    # resource:* edges from resolution are treated as template/style links.
    if kind.startswith("resource:"):
        return True
    if kind not in EXPAND_RELATION_KINDS:
        return False
    if direction == "incoming" and profile is RecipientProfile.PLANNING:
        return kind in PLANNING_EXTRA_INCOMING or kind in EXPAND_RELATION_KINDS
    if direction == "incoming" and profile is not RecipientProfile.PLANNING:
        # Implementation/compact still want callers, tests, templates.
        return kind in {
            "calls",
            "tests",
            "template_of",
            "style_of",
            "renders_component",
            "contains",
            "configured_by",
            "imports",
            "inherits",
            "implements",
            "references",
        }
    return True


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    candidates: tuple[Candidate, ...]
    hops: dict[tuple[object, ...], int]
    expansion_count: int


class RelationshipExpander:
    """Expand high-confidence seeds through snapshot-scoped relationships."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def expand(  # noqa: PLR0912
        self,
        *,
        snapshot: SnapshotRef,
        profile: RecipientProfile,
        weights: ProfileWeights,
        seeds: tuple[ScoredCandidate, ...],
        preferred_kinds: frozenset[str] | tuple[str, ...] = (),
    ) -> ExpansionResult:
        max_hops = weights.max_hops
        if max_hops <= 0:
            return ExpansionResult(candidates=(), hops={}, expansion_count=0)

        preferred = frozenset(preferred_kinds)
        merged: dict[tuple[object, ...], Candidate] = {}
        hops: dict[tuple[object, ...], int] = {}
        expansion_count = 0

        # Hop 1 from high-confidence seeds.
        hop1_seeds = [s for s in seeds if s.score >= weights.seed_score_floor]
        hop1_seeds.sort(key=lambda s: (-s.score, s.candidate.path))

        frontier: list[Candidate] = []
        for scored in hop1_seeds:
            for neighbor in self._expand_one(
                snapshot, scored.candidate, profile, preferred_kinds=preferred
            ):
                if expansion_count >= weights.max_expansions:
                    break
                identity = ranking_identity(neighbor)
                if identity in hops:
                    continue
                merge_candidate_maps(merged, neighbor)
                hops[identity] = 1
                frontier.append(neighbor)
                expansion_count += 1
            if expansion_count >= weights.max_expansions:
                break

        # Optional second hop (planning) under a hard cap.
        if (
            max_hops >= SECOND_HOP_MIN
            and weights.second_hop_expansion_cap > 0
            and expansion_count < weights.max_expansions
        ):
            second_budget = min(
                weights.second_hop_expansion_cap,
                weights.max_expansions - expansion_count,
            )
            second_added = 0
            # Re-score hop-1 to pick strongest frontier members.
            scored_frontier = sorted(
                (score_candidate(c, weights, hop=1) for c in frontier),
                key=lambda s: (-s.score, s.candidate.path),
            )
            for scored in scored_frontier:
                if scored.score < weights.seed_score_floor:
                    continue
                for neighbor in self._expand_one(
                    snapshot, scored.candidate, profile, preferred_kinds=preferred
                ):
                    if second_added >= second_budget:
                        break
                    identity = ranking_identity(neighbor)
                    if identity in hops:
                        continue
                    # Avoid re-adding the original seed set identity collisions
                    # already recorded at hop 1.
                    merge_candidate_maps(merged, neighbor)
                    hops[identity] = 2
                    second_added += 1
                    expansion_count += 1
                if second_added >= second_budget:
                    break

        return ExpansionResult(
            candidates=tuple(merged.values()),
            hops=hops,
            expansion_count=expansion_count,
        )

    def _expand_one(  # noqa: PLR0912
        self,
        snapshot: SnapshotRef,
        seed: Candidate,
        profile: RecipientProfile,
        *,
        preferred_kinds: frozenset[str] = frozenset(),
    ) -> list[Candidate]:
        neighbors: list[Candidate] = []

        if seed.unit_version_id is not None:
            for rel in list_outgoing_relationships(
                self._conn,
                snapshot_id=snapshot.snapshot_id,
                unit_version_id=seed.unit_version_id,
            ):
                if not _relation_allowed(
                    rel, profile=profile, direction="outgoing", preferred_kinds=preferred_kinds
                ):
                    continue
                neighbors.extend(self._from_outgoing(snapshot, rel, seed_path=seed.path))

            if seed.unit_id is not None:
                for rel in list_incoming_relationships(
                    self._conn,
                    snapshot_id=snapshot.snapshot_id,
                    target_unit_id=seed.unit_id,
                ):
                    if not _relation_allowed(
                        rel, profile=profile, direction="incoming", preferred_kinds=preferred_kinds
                    ):
                        continue
                    neighbors.extend(self._from_incoming(snapshot, rel, seed_path=seed.path))

        entry = get_file_version_by_path(
            self._conn, snapshot_id=snapshot.snapshot_id, relative_path=seed.path
        )
        if entry is not None:
            for rel in list_outgoing_relationships(
                self._conn,
                snapshot_id=snapshot.snapshot_id,
                file_version_id=entry.file_version.file_version_id,
            ):
                if (
                    seed.unit_version_id is not None
                    and rel.source_unit_version_id == seed.unit_version_id
                ):
                    continue
                if not _relation_allowed(
                    rel, profile=profile, direction="outgoing", preferred_kinds=preferred_kinds
                ):
                    continue
                neighbors.extend(self._from_outgoing(snapshot, rel, seed_path=seed.path))

            for rel in list_incoming_relationships(
                self._conn,
                snapshot_id=snapshot.snapshot_id,
                target_file_id=entry.file.file_id,
            ):
                if not _relation_allowed(
                    rel, profile=profile, direction="incoming", preferred_kinds=preferred_kinds
                ):
                    continue
                neighbors.extend(self._from_incoming(snapshot, rel, seed_path=seed.path))

        # Drop self.
        out: list[Candidate] = []
        for n in neighbors:
            if n.unit_version_id is not None and n.unit_version_id == seed.unit_version_id:
                continue
            if (
                n.unit_version_id is None
                and n.unit_id is None
                and n.path == seed.path
                and seed.unit_id is None
            ):
                continue
            out.append(n)
        return out

    def _from_outgoing(
        self,
        snapshot: SnapshotRef,
        rel: RelationshipRecord,
        *,
        seed_path: str,
    ) -> list[Candidate]:
        reason = f"expand:{rel.relation_kind}:outgoing"
        meta: dict[str, object] = {
            "relation_kind": rel.relation_kind,
            "direction": "outgoing",
            "seed_path": seed_path,
            "relationship_id": rel.relationship_id,
            "confidence": rel.confidence,
            "resolution_method": rel.resolution_method,
            "rel_start_line": rel.start_line,
            "rel_end_line": rel.end_line,
            "expansion_hop": 1,
        }
        if rel.target_unit_id is not None:
            resolved = unit_and_path_in_snapshot(
                self._conn,
                snapshot_id=snapshot.snapshot_id,
                unit_id=rel.target_unit_id,
            )
            if resolved is not None:
                unit, path = resolved
                # Template/style targets: record owning component path.
                if rel.relation_kind in {"template_of", "style_of"} or rel.relation_kind.startswith(
                    "resource:"
                ):
                    meta["owning_component_path"] = seed_path
                return [
                    Candidate(
                        path=path,
                        unit_id=unit.unit_id,
                        unit_version_id=unit.unit_version_id,
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                        reasons=(reason,),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_DIRECT_STRUCTURAL,
                        metadata={**meta, "qualified_name": unit.qualified_name},
                    )
                ]
        if rel.target_file_id is not None:
            target_path = path_for_file_id(self._conn, rel.target_file_id)
            if target_path is not None:
                if rel.relation_kind in {"template_of", "style_of"} or rel.relation_kind.startswith(
                    "resource:"
                ):
                    meta["owning_component_path"] = seed_path
                start, end = _pair_lines(rel.start_line, rel.end_line)
                return [
                    Candidate(
                        path=target_path,
                        unit_id=None,
                        unit_version_id=None,
                        start_line=start,
                        end_line=end,
                        candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                        reasons=(reason,),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_DIRECT_STRUCTURAL,
                        metadata=meta,
                    )
                ]
        return []

    def _from_incoming(
        self,
        snapshot: SnapshotRef,
        rel: RelationshipRecord,
        *,
        seed_path: str,
    ) -> list[Candidate]:
        reason = f"expand:{rel.relation_kind}:incoming"
        meta: dict[str, object] = {
            "relation_kind": rel.relation_kind,
            "direction": "incoming",
            "seed_path": seed_path,
            "relationship_id": rel.relationship_id,
            "confidence": rel.confidence,
            "resolution_method": rel.resolution_method,
            "rel_start_line": rel.start_line,
            "rel_end_line": rel.end_line,
            "expansion_hop": 1,
        }
        if rel.source_unit_version_id is not None:
            row = self._conn.execute(
                """
                SELECT suv.unit_version_id, suv.unit_id, suv.start_line, suv.end_line,
                       suv.qualified_name, f.path
                  FROM semantic_unit_versions suv
                  JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
                  JOIN files f ON f.file_id = sf.file_id
                 WHERE sf.snapshot_id = ? AND suv.unit_version_id = ?
                """,
                (snapshot.snapshot_id, rel.source_unit_version_id),
            ).fetchone()
            if row is not None:
                return [
                    Candidate(
                        path=str(row["path"]),
                        unit_id=int(row["unit_id"]),
                        unit_version_id=int(row["unit_version_id"]),
                        start_line=int(row["start_line"]),
                        end_line=int(row["end_line"]),
                        candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                        reasons=(reason,),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_DIRECT_STRUCTURAL,
                        metadata={
                            **meta,
                            "qualified_name": str(row["qualified_name"]),
                        },
                    )
                ]
        path = path_for_file_version_id(self._conn, rel.source_file_version_id)
        if path is not None:
            start, end = _pair_lines(rel.start_line, rel.end_line)
            return [
                Candidate(
                    path=path,
                    unit_id=None,
                    unit_version_id=None,
                    start_line=start,
                    end_line=end,
                    candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                    reasons=(reason,),
                    provider=PROVIDER_ID,
                    raw_score=SCORE_DIRECT_STRUCTURAL,
                    metadata=meta,
                )
            ]
        return []


def _pair_lines(start: int | None, end: int | None) -> tuple[int | None, int | None]:
    """Normalize asymmetric line pairs to both-set or both-None."""
    if start is None and end is None:
        return None, None
    if start is None:
        return end, end
    if end is None:
        return start, start
    return start, end


__all__ = [
    "ExpansionResult",
    "PROVIDER_ID",
    "RelationshipExpander",
    "_is_filename_only",
]
