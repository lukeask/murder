"""One-hop structural neighbor expansion (Part 9).

Expands seed candidates through persisted relationships exactly once.
No PageRank, no recursive traversal.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from murder.context_compiler.candidates.exact_hints import ExactHintsProvider
from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
    SCORE_DIRECT_STRUCTURAL,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.candidates.protocols import CandidateProvider
from murder.context_compiler.candidates.resolve import (
    path_for_file_id,
    path_for_file_version_id,
    unit_and_path_in_snapshot,
)
from murder.context_compiler.extraction.models import (
    REL_CALLS,
    REL_CONFIGURED_BY,
    REL_CONTAINS,
    REL_IMPLEMENTS,
    REL_IMPORTS,
    REL_INHERITS,
    REL_RENDERS_COMPONENT,
    REL_STYLE_OF,
    REL_TEMPLATE_OF,
    REL_TESTS,
)
from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    list_incoming_relationships,
    list_outgoing_relationships,
)
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry
from murder.context_compiler.persistence.records import RelationshipRecord

PROVIDER_ID = "structural"

_REASON_BY_RELATION: dict[str, str] = {
    REL_CALLS: "structural_calls",
    REL_IMPORTS: "structural_imports",
    REL_INHERITS: "structural_inherits",
    REL_IMPLEMENTS: "structural_implements",
    REL_RENDERS_COMPONENT: "structural_renders_component",
    REL_TEMPLATE_OF: "structural_template_of",
    REL_STYLE_OF: "structural_style_of",
    REL_TESTS: "structural_tests",
    REL_CONFIGURED_BY: "structural_configured_by",
    REL_CONTAINS: "structural_contains",
    "exports": "structural_exports",
    "references": "structural_references",
    "resource:template": "structural_resource_template",
    "resource:style": "structural_resource_style",
    "resource:asset": "structural_resource_asset",
}


def _reason_for(relation_kind: str, *, direction: str) -> str:
    base = _REASON_BY_RELATION.get(relation_kind)
    if base is None:
        if relation_kind.startswith("resource:"):
            base = f"structural_{relation_kind.replace(':', '_')}"
        else:
            base = f"structural_{relation_kind}"
    return f"{base}_{direction}"


def _kind_allowed_by_hints(relation_kind: str, hints: tuple[str, ...]) -> bool:
    """True when ``hints`` is empty or ``relation_kind`` matches a hint."""
    if not hints:
        return True
    if relation_kind in hints:
        return True
    if relation_kind.startswith("resource:"):
        suffix = relation_kind.split(":", 1)[1]
        if suffix == "template" and "template_of" in hints:
            return True
        if suffix == "style" and "style_of" in hints:
            return True
    return False


@dataclass(frozen=True, slots=True)
class StructuralNeighborProvider:
    """Expand seeds one structural hop through persisted relationships.

    Seeds come from ``seed_provider`` when set; otherwise from exact path /
    symbol hints on the request. Expansion is exactly one hop.
    """

    conn: sqlite3.Connection
    seed_provider: CandidateProvider | None = None
    max_seeds: int = 40
    max_neighbors: int = 120

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        seeds = await self._seeds(request, snapshot, prior_evidence)
        if not seeds:
            return ()

        out: list[Candidate] = []
        seen: set[tuple[object, ...]] = set()

        for seed in seeds[: self.max_seeds]:
            for neighbor in self._expand_one_hop(snapshot, seed):
                rel_kind = str(neighbor.metadata.get("relation_kind") or "")
                if not _kind_allowed_by_hints(rel_kind, request.relationship_kind_hints):
                    continue
                key = (
                    neighbor.path,
                    neighbor.unit_version_id,
                    neighbor.unit_id,
                    neighbor.reasons,
                )
                if key in seen:
                    continue
                # Skip the seed itself when identity matches.
                if (
                    neighbor.unit_version_id is not None
                    and neighbor.unit_version_id == seed.unit_version_id
                ):
                    continue
                if (
                    neighbor.unit_version_id is None
                    and neighbor.unit_id is None
                    and neighbor.path == seed.path
                    and seed.unit_id is None
                ):
                    continue
                seen.add(key)
                out.append(neighbor)
                if len(out) >= self.max_neighbors:
                    return tuple(out)
        return tuple(out)

    async def _seeds(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        if self.seed_provider is not None:
            return await self.seed_provider.generate(request, snapshot, prior_evidence)
        return await ExactHintsProvider(self.conn).generate(request, snapshot, prior_evidence)

    def _expand_one_hop(self, snapshot: SnapshotRef, seed: Candidate) -> list[Candidate]:
        neighbors: list[Candidate] = []

        if seed.unit_version_id is not None:
            outgoing = list_outgoing_relationships(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                unit_version_id=seed.unit_version_id,
            )
            for rel in outgoing:
                neighbors.extend(self._from_outgoing(snapshot, rel, seed_path=seed.path))

            if seed.unit_id is not None:
                incoming = list_incoming_relationships(
                    self.conn,
                    snapshot_id=snapshot.snapshot_id,
                    target_unit_id=seed.unit_id,
                )
                for rel in incoming:
                    neighbors.extend(self._from_incoming(snapshot, rel, seed_path=seed.path))

        entry = get_file_version_by_path(
            self.conn, snapshot_id=snapshot.snapshot_id, relative_path=seed.path
        )
        if entry is not None:
            file_outgoing = list_outgoing_relationships(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                file_version_id=entry.file_version.file_version_id,
            )
            for rel in file_outgoing:
                # Avoid double-counting unit-scoped edges already handled.
                if (
                    seed.unit_version_id is not None
                    and rel.source_unit_version_id == seed.unit_version_id
                ):
                    continue
                neighbors.extend(self._from_outgoing(snapshot, rel, seed_path=seed.path))

            incoming_file = list_incoming_relationships(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                target_file_id=entry.file.file_id,
            )
            for rel in incoming_file:
                neighbors.extend(self._from_incoming(snapshot, rel, seed_path=seed.path))

        return neighbors

    def _from_outgoing(
        self,
        snapshot: SnapshotRef,
        rel: RelationshipRecord,
        *,
        seed_path: str,
    ) -> list[Candidate]:
        reason = _reason_for(rel.relation_kind, direction="outgoing")
        meta = {
            "relation_kind": rel.relation_kind,
            "direction": "outgoing",
            "seed_path": seed_path,
            "relationship_id": rel.relationship_id,
            "confidence": rel.confidence,
        }
        results: list[Candidate] = []
        if rel.target_unit_id is not None:
            resolved = unit_and_path_in_snapshot(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                unit_id=rel.target_unit_id,
            )
            if resolved is not None:
                unit, path = resolved
                results.append(
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
                        metadata={
                            **meta,
                            "qualified_name": unit.qualified_name,
                        },
                    )
                )
                return results
        if rel.target_file_id is not None:
            target_path = path_for_file_id(self.conn, rel.target_file_id)
            if target_path is not None:
                results.append(
                    Candidate(
                        path=target_path,
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                        reasons=(reason,),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_DIRECT_STRUCTURAL,
                        metadata=meta,
                    )
                )
        return results

    def _from_incoming(
        self,
        snapshot: SnapshotRef,
        rel: RelationshipRecord,
        *,
        seed_path: str,
    ) -> list[Candidate]:
        # Incoming: the neighbor is the relationship source.
        reason = _reason_for(rel.relation_kind, direction="incoming")
        # Map common inverse labels for clarity.
        inverse = {
            REL_CALLS: "structural_called_by",
            REL_IMPORTS: "structural_imported_by",
            REL_TESTS: "structural_tested_by",
            REL_CONTAINS: "structural_parent_of",
        }.get(rel.relation_kind)
        if inverse:
            reason = inverse

        meta = {
            "relation_kind": rel.relation_kind,
            "direction": "incoming",
            "seed_path": seed_path,
            "relationship_id": rel.relationship_id,
            "confidence": rel.confidence,
        }
        results: list[Candidate] = []

        if rel.source_unit_version_id is not None:
            # Resolve unit version → logical unit → path in snapshot.
            row = self.conn.execute(
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
                results.append(
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
                )
                return results

        path = path_for_file_version_id(self.conn, rel.source_file_version_id)
        if path is not None:
            results.append(
                Candidate(
                    path=path,
                    unit_id=None,
                    unit_version_id=None,
                    start_line=None,
                    end_line=None,
                    candidate_kind=CANDIDATE_KIND_RELATIONSHIP_NEIGHBOR,
                    reasons=(reason,),
                    provider=PROVIDER_ID,
                    raw_score=SCORE_DIRECT_STRUCTURAL,
                    metadata=meta,
                )
            )
        return results


__all__ = ["StructuralNeighborProvider", "PROVIDER_ID"]
