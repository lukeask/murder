"""Focused test relationship / naming-heuristic provider (Part 9)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from murder.context_compiler.candidates.exact_hints import ExactHintsProvider
from murder.context_compiler.candidates.models import (
    CANDIDATE_KIND_TEST,
    SCORE_FOCUSED_TEST,
    SCORE_WEAK_TEXTUAL,
    Candidate,
    SnapshotRef,
)
from murder.context_compiler.candidates.protocols import CandidateProvider
from murder.context_compiler.candidates.resolve import (
    path_for_file_id,
    path_for_file_version_id,
    snapshot_paths,
    unit_and_path_in_snapshot,
)
from murder.context_compiler.extraction.models import REL_TESTS
from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    list_incoming_relationships,
    list_outgoing_relationships,
)
from murder.context_compiler.models import ContextRequest, EvidenceLedgerEntry

PROVIDER_ID = "tests"

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]+$|[^/]+_test\.[^/]+$|"
    r"[^/]+\.(test|spec)\.[^/]+$",
    re.IGNORECASE,
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path.replace("\\", "/")))


def production_stem_from_test(path: str) -> str | None:
    """Derive a likely production module stem from a test filename."""
    name = PurePosixPath(path.replace("\\", "/")).name
    lower = name.lower()
    stem = PurePosixPath(name).stem
    # Strip compound extensions: foo.test.ts → foo, foo_test.go → foo
    for suffix in (".test", ".spec"):
        if lower.endswith(suffix + PurePosixPath(name).suffix.lower()) or stem.lower().endswith(
            suffix
        ):
            # e.g. foo.test.tsx → stem foo.test → strip .test
            base = stem
            for s in (".test", ".spec"):
                if base.lower().endswith(s):
                    base = base[: -len(s)]
            return base or None
    if stem.startswith("test_"):
        return stem[5:] or None
    if stem.endswith("_test"):
        return stem[:-5] or None
    # tests/foo.py → foo
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if any(p.lower() in {"tests", "test", "__tests__", "spec"} for p in parts[:-1]):
        return stem or None
    return None


def test_name_patterns_for_production(path: str) -> list[str]:
    """Filename patterns that conventionally test ``path``."""
    posix = path.replace("\\", "/")
    stem = PurePosixPath(posix).stem
    parent = str(PurePosixPath(posix).parent)
    suffix = PurePosixPath(posix).suffix
    patterns = [
        f"test_{stem}{suffix}",
        f"{stem}_test{suffix}",
        f"{stem}.test{suffix}",
        f"{stem}.spec{suffix}",
    ]
    # TSX/JSX often tested as .test.ts / .test.tsx
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        patterns.extend(
            [
                f"{stem}.test.ts",
                f"{stem}.test.tsx",
                f"{stem}.spec.ts",
                f"{stem}.spec.tsx",
                f"{stem}.test.js",
                f"{stem}.test.jsx",
            ]
        )
    if suffix == ".go":
        patterns.append(f"{stem}_test.go")
    if suffix == ".py":
        patterns.extend([f"test_{stem}.py", f"{stem}_test.py"])

    # Directory conventions relative to the production file.
    dir_prefixes = [
        f"tests/{stem}",
        f"test/{stem}",
        f"__tests__/{stem}",
        f"spec/{stem}",
    ]
    if parent and parent != ".":
        dir_prefixes.extend(
            [
                f"{parent}/tests/{stem}",
                f"{parent}/__tests__/{stem}",
                f"{parent}/test_{stem}",
                f"{parent}/{stem}_test",
                f"{parent}/{stem}.test",
                f"{parent}/{stem}.spec",
            ]
        )
    return list(dict.fromkeys([*patterns, *dir_prefixes]))


def _signals_for_persisted_tests_edge(
    resolution_method: str,
) -> tuple[tuple[str, ...], float]:
    """Map persisted ``tests`` resolution method → candidate reasons/score."""
    if resolution_method == "test_filename_heuristic":
        return ("test_filename_heuristic",), SCORE_WEAK_TEXTUAL
    return ("persisted_tests_relationship", "explicit_test_evidence"), SCORE_FOCUSED_TEST


@dataclass(frozen=True, slots=True)
class TestRelationshipProvider:
    """Find likely focused tests via persisted ``tests`` edges and naming.

    Naming-only matches carry explicit heuristic reasons and must not be
    presented as exact semantic relationships.
    """

    conn: sqlite3.Connection
    seed_provider: CandidateProvider | None = None
    max_candidates: int = 80

    async def generate(
        self,
        request: ContextRequest,
        snapshot: SnapshotRef,
        prior_evidence: Sequence[EvidenceLedgerEntry],
    ) -> Sequence[Candidate]:
        seeds = await self._seeds(request, snapshot, prior_evidence)
        if not seeds:
            # Still allow objective path fragments via request hints alone.
            seeds = ()

        out: list[Candidate] = []
        seen: set[tuple[object, ...]] = set()
        seed_paths = list(dict.fromkeys(c.path for c in seeds if c.path))
        # Also use raw path hints as production seeds.
        for hint in request.path_hints:
            hint_n = hint.strip().replace("\\", "/")
            if hint_n and hint_n not in seed_paths:
                seed_paths.append(hint_n)

        for path in seed_paths:
            if is_test_path(path):
                continue
            for candidate in self._persisted_tests(snapshot, path):
                key = (candidate.path, candidate.unit_version_id, candidate.reasons)
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
                if len(out) >= self.max_candidates:
                    return tuple(out)

            for candidate in self._naming_heuristic_tests(snapshot, path):
                key = (candidate.path, candidate.unit_version_id, candidate.reasons)
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
                if len(out) >= self.max_candidates:
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
        if request.path_hints or request.symbol_hints:
            return await ExactHintsProvider(self.conn).generate(request, snapshot, prior_evidence)
        return ()

    def _persisted_tests(  # noqa: PLR0912
        self, snapshot: SnapshotRef, production_path: str
    ) -> list[Candidate]:
        results: list[Candidate] = []
        entry = get_file_version_by_path(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            relative_path=production_path,
        )
        if entry is None:
            # Try suffix uniqueness among snapshot paths.
            matches = [
                p
                for p in snapshot_paths(self.conn, snapshot.snapshot_id)
                if p == production_path or p.endswith("/" + production_path)
            ]
            if len(matches) != 1:
                return results
            entry = get_file_version_by_path(
                self.conn,
                snapshot_id=snapshot.snapshot_id,
                relative_path=matches[0],
            )
            if entry is None:
                return results
            production_path = matches[0]

        # Incoming: test files whose ``tests`` edge targets this production file.
        incoming = list_incoming_relationships(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            target_file_id=entry.file.file_id,
        )
        for rel in incoming:
            if rel.relation_kind != REL_TESTS:
                continue
            path = None
            unit_id = None
            unit_version_id = None
            start = end = None
            if rel.source_unit_version_id is not None:
                row = self.conn.execute(
                    """
                    SELECT suv.unit_version_id, suv.unit_id, suv.start_line, suv.end_line,
                           f.path
                      FROM semantic_unit_versions suv
                      JOIN snapshot_files sf ON sf.file_version_id = suv.file_version_id
                      JOIN files f ON f.file_id = sf.file_id
                     WHERE sf.snapshot_id = ? AND suv.unit_version_id = ?
                    """,
                    (snapshot.snapshot_id, rel.source_unit_version_id),
                ).fetchone()
                if row is not None:
                    path = str(row["path"])
                    unit_id = int(row["unit_id"])
                    unit_version_id = int(row["unit_version_id"])
                    start = int(row["start_line"])
                    end = int(row["end_line"])
            if path is None:
                path = path_for_file_version_id(self.conn, rel.source_file_version_id)
            if path is None:
                continue
            reasons, raw_score = _signals_for_persisted_tests_edge(rel.resolution_method)
            results.append(
                Candidate(
                    path=path,
                    unit_id=unit_id,
                    unit_version_id=unit_version_id,
                    start_line=start,
                    end_line=end,
                    candidate_kind=CANDIDATE_KIND_TEST,
                    reasons=reasons,
                    provider=PROVIDER_ID,
                    raw_score=raw_score,
                    metadata={
                        "production_path": production_path,
                        "relationship_id": rel.relationship_id,
                        "resolution_method": rel.resolution_method,
                        "confidence": rel.confidence,
                    },
                )
            )

        # Outgoing from production (unusual but possible): follow ``tests`` edges.
        outgoing = list_outgoing_relationships(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            file_version_id=entry.file_version.file_version_id,
        )
        for rel in outgoing:
            if rel.relation_kind != REL_TESTS:
                continue
            if rel.target_unit_id is not None:
                resolved = unit_and_path_in_snapshot(
                    self.conn,
                    snapshot_id=snapshot.snapshot_id,
                    unit_id=rel.target_unit_id,
                )
                if resolved is None:
                    continue
                unit, path = resolved
                results.append(
                    Candidate(
                        path=path,
                        unit_id=unit.unit_id,
                        unit_version_id=unit.unit_version_id,
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        candidate_kind=CANDIDATE_KIND_TEST,
                        reasons=("persisted_tests_relationship_outgoing",),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_FOCUSED_TEST,
                        metadata={
                            "production_path": production_path,
                            "relationship_id": rel.relationship_id,
                        },
                    )
                )
            elif rel.target_file_id is not None:
                path = path_for_file_id(self.conn, rel.target_file_id)
                if path is None:
                    continue
                results.append(
                    Candidate(
                        path=path,
                        unit_id=None,
                        unit_version_id=None,
                        start_line=None,
                        end_line=None,
                        candidate_kind=CANDIDATE_KIND_TEST,
                        reasons=("persisted_tests_relationship_outgoing",),
                        provider=PROVIDER_ID,
                        raw_score=SCORE_FOCUSED_TEST,
                        metadata={
                            "production_path": production_path,
                            "relationship_id": rel.relationship_id,
                        },
                    )
                )
        return results

    def _naming_heuristic_tests(  # noqa: PLR0912
        self, snapshot: SnapshotRef, production_path: str
    ) -> list[Candidate]:
        entry = get_file_version_by_path(
            self.conn,
            snapshot_id=snapshot.snapshot_id,
            relative_path=production_path,
        )
        resolved_path = entry.file.path if entry is not None else production_path
        patterns = test_name_patterns_for_production(resolved_path)
        all_paths = snapshot_paths(self.conn, snapshot.snapshot_id)
        results: list[Candidate] = []

        for candidate_path in all_paths:
            if candidate_path == resolved_path:
                continue
            if not is_test_path(candidate_path):
                continue
            name = PurePosixPath(candidate_path).name
            stem_match = False
            for pattern in patterns:
                # pattern may be a bare filename or a path prefix/stem.
                if candidate_path == pattern or candidate_path.endswith("/" + pattern):
                    stem_match = True
                    break
                if name == pattern or name.startswith(pattern):
                    stem_match = True
                    break
                # Prefix without extension: tests/foo matches tests/foo.py
                if candidate_path.startswith(pattern + ".") or candidate_path.startswith(
                    pattern + "/"
                ):
                    stem_match = True
                    break
            if not stem_match:
                # Nearby package: same parent stem convention.
                prod_stem = PurePosixPath(resolved_path).stem.lower()
                test_stem = production_stem_from_test(candidate_path)
                if test_stem and test_stem.lower() == prod_stem:
                    # Same or sibling package directory.
                    prod_parent = str(PurePosixPath(resolved_path).parent)
                    test_parent = str(PurePosixPath(candidate_path).parent)
                    if prod_parent == test_parent or test_parent.endswith(
                        ("/tests", "/test", "/__tests__", "/spec")
                    ):
                        stem_match = True
            if not stem_match:
                continue
            results.append(
                Candidate(
                    path=candidate_path,
                    unit_id=None,
                    unit_version_id=None,
                    start_line=None,
                    end_line=None,
                    candidate_kind=CANDIDATE_KIND_TEST,
                    reasons=("test_filename_heuristic",),
                    provider=PROVIDER_ID,
                    raw_score=SCORE_FOCUSED_TEST,
                    metadata={
                        "production_path": resolved_path,
                        "heuristic": "filename_convention",
                    },
                )
            )

        # Weak: test under nearby tests/ importing same stem (path-only).
        if not results:
            stem = PurePosixPath(resolved_path).stem
            for candidate_path in all_paths:
                if not is_test_path(candidate_path):
                    continue
                if stem.lower() in PurePosixPath(candidate_path).name.lower():
                    results.append(
                        Candidate(
                            path=candidate_path,
                            unit_id=None,
                            unit_version_id=None,
                            start_line=None,
                            end_line=None,
                            candidate_kind=CANDIDATE_KIND_TEST,
                            reasons=("weak_test_name_substring",),
                            provider=PROVIDER_ID,
                            raw_score=SCORE_WEAK_TEXTUAL,
                            metadata={
                                "production_path": resolved_path,
                                "heuristic": "name_substring",
                            },
                        )
                    )
        return results


__all__ = [
    "PROVIDER_ID",
    "TestRelationshipProvider",
    "is_test_path",
    "production_stem_from_test",
    "test_name_patterns_for_production",
]
