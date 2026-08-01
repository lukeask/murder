"""Repository-level best-effort resolution for a building snapshot.

Local within-file resolution belongs to extractors. This pass runs after all
files in a snapshot are attached and resolves:

* relative import paths → logical files / exported units;
* identifier references → candidate units (with ambiguity limits);
* unresolved resource paths → files in the snapshot;
* test→production relationships: explicit import/call/render evidence at
  exact/inferred tiers, with filename-stem affinity only as ``weak``.

Writes only to ``resolved_relationships`` and ``resolved_reference_targets``,
always keyed by ``snapshot_id``. Rerunning replaces those rows transactionally
and never mutates extraction facts. Confidence is a tier derived from
precedence — never a float probability.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath

from murder.context_compiler.extraction.models import REL_IMPORTS, REL_RENDERS_COMPONENT, REL_TESTS
from murder.context_compiler.indexing.resolution_policy import (
    CONFIDENCE_EXACT,
    MAX_REFERENCE_CANDIDATES,
    MAX_UNQUALIFIED_LOOKUP,
    MIN_IDENTIFIER_LEN,
    PRECEDENCE_EXACT_PATH,
    PRECEDENCE_FILENAME_HEURISTIC,
    PRECEDENCE_FRAMEWORK_SELECTOR,
    PRECEDENCE_IMPORTED_ALIAS,
    PRECEDENCE_UNIQUE_UNQUALIFIED,
    ConfidenceTier,
    tier_for_precedence,
    tier_rank,
)
from murder.context_compiler.indexing.state import ResolutionSummary
from murder.context_compiler.persistence.files import (
    list_snapshot_file_versions,
    list_snapshot_files,
    normalize_relative_path,
)
from murder.context_compiler.persistence.relationships import (
    clear_resolved_rows_for_snapshot,
    insert_resolved_reference_target,
    insert_resolved_relationship,
    list_imports_for_file_version,
    list_references_for_file_version,
    list_resource_links_for_file_version,
)
from murder.context_compiler.persistence.semantic_units import (
    list_semantic_unit_versions_for_file_version,
)

_COMMON_IDENTIFIERS = frozenset(
    {
        "a",
        "b",
        "c",
        "i",
        "j",
        "k",
        "n",
        "x",
        "y",
        "z",
        "id",
        "key",
        "val",
        "value",
        "data",
        "item",
        "items",
        "result",
        "results",
        "error",
        "err",
        "self",
        "this",
        "cls",
        "args",
        "kwargs",
        "true",
        "false",
        "none",
        "null",
        "undefined",
        "type",
        "name",
        "path",
        "file",
        "config",
        "options",
        "params",
        "index",
        "count",
        "size",
        "length",
        "get",
        "set",
        "add",
        "remove",
        "update",
        "create",
        "delete",
        "load",
        "save",
        "run",
        "main",
        "test",
        "init",
        "str",
        "int",
        "list",
        "dict",
        "map",
        "object",
        "string",
        "number",
        "boolean",
        "function",
        "class",
        "module",
        "exports",
        "require",
        "import",
        "from",
        "return",
        "print",
        "log",
        "debug",
        "info",
        "warn",
        "exception",
    }
)

_SOURCE_EXTENSIONS = (
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".vue",
    ".svelte",
    ".html",
    ".css",
)

_INDEX_BASENAMES = (
    "__init__.py",
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
    "mod.rs",
    "main.go",
)

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]+$|[^/]+_test\.[^/]+$|"
    r"[^/]+\.(test|spec)\.[^/]+$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _UnitInfo:
    unit_id: int
    unit_version_id: int
    file_id: int
    file_version_id: int
    path: str
    qualified_name: str
    unqualified_name: str
    exported: bool
    semantic_role: str | None
    selectors: tuple[str, ...]
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    info: _UnitInfo
    precedence: int
    method: str

    @property
    def confidence(self) -> ConfidenceTier:
        return tier_for_precedence(self.precedence)


@dataclass
class _SnapshotIndex:
    path_to_file_id: dict[str, int]
    file_id_to_path: dict[int, str]
    file_id_to_version_id: dict[int, int]
    units_by_file: dict[int, list[_UnitInfo]]
    by_qualified: dict[str, list[_UnitInfo]]
    by_unqualified: dict[str, list[_UnitInfo]]
    by_exported_unqualified: dict[str, list[_UnitInfo]]
    by_selector: dict[str, list[_UnitInfo]]
    stem_to_paths: dict[str, list[str]]


def _selectors_from_metadata(metadata_json: str) -> tuple[str, ...]:
    """Pull Angular-style selector / resolution_keys from unit metadata."""
    if not metadata_json or metadata_json == "{}":
        return ()
    try:
        meta = json.loads(metadata_json)
    except json.JSONDecodeError:
        return ()
    if not isinstance(meta, dict):
        return ()
    found: list[str] = []
    selector = meta.get("selector")
    if isinstance(selector, str) and selector:
        found.append(selector)
    keys = meta.get("resolution_keys")
    if isinstance(keys, (list, tuple)):
        for key in keys:
            if isinstance(key, str) and key:
                found.append(key)
    angular = meta.get("angular")
    if isinstance(angular, dict):
        ang_sel = angular.get("selector")
        if isinstance(ang_sel, str) and ang_sel:
            found.append(ang_sel)
    # Preserve order, drop dupes.
    return tuple(dict.fromkeys(found))


def _build_snapshot_index(conn: sqlite3.Connection, snapshot_id: int) -> _SnapshotIndex:
    path_to_file_id: dict[str, int] = {}
    file_id_to_path: dict[int, str] = {}
    file_id_to_version_id: dict[int, int] = {}
    stem_to_paths: dict[str, list[str]] = {}

    for sf in list_snapshot_files(conn, snapshot_id):
        row = conn.execute(
            "SELECT path FROM files WHERE file_id = ?",
            (sf.file_id,),
        ).fetchone()
        if row is None:
            continue
        path = str(row["path"])
        path_to_file_id[path] = sf.file_id
        file_id_to_path[sf.file_id] = path
        file_id_to_version_id[sf.file_id] = sf.file_version_id
        stem = PurePosixPath(path).stem.lower()
        stem_to_paths.setdefault(stem, []).append(path)

    units_by_file: dict[int, list[_UnitInfo]] = {}
    by_qualified: dict[str, list[_UnitInfo]] = {}
    by_unqualified: dict[str, list[_UnitInfo]] = {}
    by_exported: dict[str, list[_UnitInfo]] = {}
    by_selector: dict[str, list[_UnitInfo]] = {}

    for fv in list_snapshot_file_versions(conn, snapshot_id):
        path = file_id_to_path.get(fv.file_id, "")
        for uv in list_semantic_unit_versions_for_file_version(conn, fv.file_version_id):
            selectors = _selectors_from_metadata(uv.metadata_json)
            info = _UnitInfo(
                unit_id=uv.unit_id,
                unit_version_id=uv.unit_version_id,
                file_id=fv.file_id,
                file_version_id=fv.file_version_id,
                path=path,
                qualified_name=uv.qualified_name,
                unqualified_name=uv.unqualified_name,
                exported=uv.exported,
                semantic_role=uv.semantic_role,
                selectors=selectors,
                start_line=uv.start_line,
                end_line=uv.end_line,
            )
            units_by_file.setdefault(fv.file_id, []).append(info)
            by_qualified.setdefault(uv.qualified_name, []).append(info)
            by_unqualified.setdefault(uv.unqualified_name, []).append(info)
            if uv.exported:
                by_exported.setdefault(uv.unqualified_name, []).append(info)
            for sel in selectors:
                by_selector.setdefault(sel, []).append(info)

    return _SnapshotIndex(
        path_to_file_id=path_to_file_id,
        file_id_to_path=file_id_to_path,
        file_id_to_version_id=file_id_to_version_id,
        units_by_file=units_by_file,
        by_qualified=by_qualified,
        by_unqualified=by_unqualified,
        by_exported_unqualified=by_exported,
        by_selector=by_selector,
        stem_to_paths=stem_to_paths,
    )


def _candidate_import_paths(source_path: str, specifier: str) -> list[str]:  # noqa: PLR0912
    """Expand a module specifier into candidate worktree-relative paths."""
    spec = specifier.strip()
    if not spec or spec.startswith(("http://", "https://", "node:", "data:")):
        return []

    source_dir = PurePosixPath(source_path).parent
    if str(source_dir) == ".":
        source_dir = PurePosixPath("")

    if spec.startswith("@/"):
        base = PurePosixPath(spec[2:])
    elif spec.startswith("/"):
        base = PurePosixPath(spec.lstrip("/"))
    elif spec.startswith("."):
        # Relative to source directory.
        joined = (source_dir / spec).as_posix() if str(source_dir) else spec
        try:
            base = PurePosixPath(normalize_relative_path(joined))
        except ValueError:
            # Manual collapse for ``../`` that normalize rejects mid-resolution.
            parts: list[str] = []
            for part in PurePosixPath(
                (source_dir / spec).as_posix() if str(source_dir) else spec
            ).parts:
                if part in ("", "."):
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(part)
            if not parts:
                return []
            base = PurePosixPath("/".join(parts))
    else:
        # Package-relative or bare local module (``from util import x``).
        # Unmatched names simply miss the snapshot — no external package graph.
        base = PurePosixPath(spec.replace(".", "/"))

    candidates: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        try:
            normalized = normalize_relative_path(path)
        except ValueError:
            return
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    base_s = base.as_posix()
    add(base_s)
    if not any(base_s.endswith(ext) for ext in _SOURCE_EXTENSIONS):
        for ext in _SOURCE_EXTENSIONS:
            add(f"{base_s}{ext}")
        for index_name in _INDEX_BASENAMES:
            add(f"{base_s}/{index_name}")

    # Also try from source directory for non-relative specs.
    if not spec.startswith((".", "/", "@/")):
        local_base = PurePosixPath(spec.replace(".", "/"))
        local = (source_dir / local_base).as_posix() if str(source_dir) else local_base.as_posix()
        add(local)
        if not any(local.endswith(ext) for ext in _SOURCE_EXTENSIONS):
            for ext in _SOURCE_EXTENSIONS:
                add(f"{local}{ext}")
            for index_name in _INDEX_BASENAMES:
                add(f"{local}/{index_name}")

    return candidates


def _resolve_import_file(index: _SnapshotIndex, source_path: str, specifier: str) -> int | None:
    for candidate in _candidate_import_paths(source_path, specifier):
        file_id = index.path_to_file_id.get(candidate)
        if file_id is not None:
            return file_id
    return None


def _exported_units_named(index: _SnapshotIndex, target_file_id: int, name: str) -> list[_UnitInfo]:
    return [
        u
        for u in index.units_by_file.get(target_file_id, [])
        if u.exported
        and (
            name in (u.unqualified_name, u.qualified_name) or u.qualified_name.endswith(f".{name}")
        )
    ]


def _dedupe_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    """Keep highest-precedence (lowest rank number) per unit_id."""
    best_by_unit: dict[int, _Candidate] = {}
    for cand in candidates:
        prev = best_by_unit.get(cand.info.unit_id)
        if prev is None or cand.precedence < prev.precedence:
            best_by_unit[cand.info.unit_id] = cand
    return sorted(
        best_by_unit.values(),
        key=lambda c: (c.precedence, c.info.unit_id),
    )


def _write_reference_targets(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    reference_id: int,
    candidates: list[_Candidate],
    stats: dict[str, int],
) -> None:
    if not candidates:
        return
    ranked = candidates[:MAX_REFERENCE_CANDIDATES]
    # Preferred only when a single top-tier survivor exists. Ambiguity =
    # multiple targets at the best precedence → mark none preferred.
    best_precedence = ranked[0].precedence
    top = [c for c in ranked if c.precedence == best_precedence]
    preferred_unit_id = top[0].info.unit_id if len(top) == 1 else None

    for cand in ranked:
        is_preferred = preferred_unit_id is not None and cand.info.unit_id == preferred_unit_id
        try:
            insert_resolved_reference_target(
                conn,
                snapshot_id=snapshot_id,
                reference_id=reference_id,
                target_unit_id=cand.info.unit_id,
                confidence=cand.confidence,
                resolution_method=cand.method,
                is_preferred=is_preferred,
            )
        except sqlite3.IntegrityError:
            continue
        stats["reference_targets_written"] += 1


def _should_skip_identifier(identifier: str) -> bool:
    name = identifier.strip()
    if len(name) < MIN_IDENTIFIER_LEN:
        return True
    if name.lower() in _COMMON_IDENTIFIERS:
        return True
    if name.isnumeric():
        return True
    return False


def _component_tag_label(ref: object) -> str:
    """Original template tag when present (e.g. kebab-case); else identifier."""
    identifier = getattr(ref, "identifier", "") or ""
    raw = getattr(ref, "metadata_json", None) or "{}"
    try:
        meta = json.loads(raw) if isinstance(raw, str) else {}
    except json.JSONDecodeError:
        return identifier
    if isinstance(meta, dict):
        tag = meta.get("tag")
        if isinstance(tag, str) and tag:
            return tag
    return identifier


def _production_stem_from_test(path: str) -> str | None:
    name = PurePosixPath(path).name
    stem = PurePosixPath(path).stem
    low = stem.lower()
    for suffix in (".test", ".spec"):
        if low.endswith(suffix):
            return stem[: -len(suffix)]
    if low.startswith("test_"):
        return stem[5:]
    if low.endswith("_test"):
        return stem[:-5]
    if name.startswith("test_") or "_test." in name or ".test." in name or ".spec." in name:
        pass
    if _TEST_PATH_RE.search(path):
        return stem.removeprefix("test_").removesuffix("_test")
    return None


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path.replace("\\", "/")))


def _is_production_path(path: str) -> bool:
    return bool(path) and not _is_test_path(path)


@dataclass(frozen=True, slots=True)
class _ExplicitTestEvidence:
    """Best explicit test→production signal seen for one production file."""

    target_file_id: int
    confidence: ConfidenceTier
    resolution_method: str
    target_unit_id: int | None = None
    source_unit_version_id: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: dict[str, object] | None = None


def _prefer_explicit_test_evidence(
    current: _ExplicitTestEvidence | None,
    candidate: _ExplicitTestEvidence,
) -> _ExplicitTestEvidence:
    """Keep the stronger tier; prefer unit-scoped evidence when tiers tie."""
    if current is None:
        return candidate
    cur_rank = tier_rank(current.confidence)
    new_rank = tier_rank(candidate.confidence)
    if new_rank > cur_rank:
        return candidate
    if new_rank < cur_rank:
        return current
    if candidate.target_unit_id is not None and current.target_unit_id is None:
        return candidate
    return current


def _alias_map_for_file(
    conn: sqlite3.Connection,
    index: _SnapshotIndex,
    *,
    source_path: str,
    file_version_id: int,
) -> dict[str, list[_UnitInfo]]:
    """Map local import aliases / imported names → exported units in targets."""
    aliases: dict[str, list[_UnitInfo]] = {}
    for imp in list_imports_for_file_version(conn, file_version_id):
        target_file_id = _resolve_import_file(index, source_path, imp.module_specifier)
        if target_file_id is None:
            continue
        # Local binding name used in the source file.
        local_name = imp.local_alias or imp.imported_name
        if not local_name:
            continue
        # Symbol looked up in the target module.
        exported_name = imp.imported_name or imp.local_alias
        if not exported_name:
            continue
        units = _exported_units_named(index, target_file_id, exported_name)
        if units:
            aliases.setdefault(local_name, []).extend(units)
    return aliases


def _resolve_template_selectors(
    conn: sqlite3.Connection,
    *,
    index: _SnapshotIndex,
    snapshot_id: int,
    source_file_version_id: int,
    source_unit_version_id: int | None,
    template_file_id: int,
    stats: dict[str, int],
) -> None:
    """Match custom-element tags in an external template to component selectors.

    Colliding selectors persist all candidates; none become a preferred single
    edge — callers see fan-out via multiple ``renders_component`` rows.
    """
    for tag_unit in index.units_by_file.get(template_file_id, []):
        tag = tag_unit.unqualified_name
        if not tag or tag_unit.semantic_role in {"component", "directive"}:
            continue
        # Custom elements / attribute-style tags typically contain a hyphen.
        hits = [
            u
            for u in index.by_selector.get(tag, [])
            if u.file_id != template_file_id and u.semantic_role in {"component", "directive"}
        ]
        if not hits:
            continue
        if len(hits) > MAX_UNQUALIFIED_LOOKUP:
            stats["skipped_ambiguous"] += 1
            continue
        if len(hits) > 1:
            stats["skipped_ambiguous"] += 1
        for info in hits[:MAX_REFERENCE_CANDIDATES]:
            insert_resolved_relationship(
                conn,
                snapshot_id=snapshot_id,
                source_file_version_id=source_file_version_id,
                source_unit_version_id=source_unit_version_id,
                relation_kind=REL_RENDERS_COMPONENT,
                confidence=tier_for_precedence(PRECEDENCE_FRAMEWORK_SELECTOR),
                resolution_method="framework_selector",
                target_file_id=info.file_id,
                target_unit_id=info.unit_id,
                start_line=tag_unit.start_line,
                end_line=tag_unit.end_line,
                metadata={"selector": tag, "template_file_id": template_file_id},
            )
            stats["relationships_added"] += 1


def resolve_snapshot(  # noqa: PLR0912, PLR0915
    conn: sqlite3.Connection,
    snapshot_id: int,
) -> ResolutionSummary:
    """Run repository-level resolution against files attached to ``snapshot_id``.

    Idempotent: clears prior resolved rows for this snapshot, then rewrites
    them. Extraction facts are never mutated. Composable with an ambient
    transaction (the coordinator already wraps this call).
    """
    index = _build_snapshot_index(conn, snapshot_id)
    stats = {
        "imports_resolved_to_files": 0,
        "imported_names_resolved": 0,
        "reference_targets_written": 0,
        "relationships_added": 0,
        "resource_links_resolved": 0,
        "skipped_ambiguous": 0,
    }

    owns_transaction = conn.isolation_level is None and not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        clear_resolved_rows_for_snapshot(conn, snapshot_id)

        for file_id, file_version_id in index.file_id_to_version_id.items():
            source_path = index.file_id_to_path.get(file_id, "")
            source_is_test = _is_test_path(source_path)
            # Best explicit test→production evidence for this source file.
            # Filename affinity is applied later only for uncovered targets.
            explicit_test_targets: dict[int, _ExplicitTestEvidence] = {}
            alias_map = _alias_map_for_file(
                conn,
                index,
                source_path=source_path,
                file_version_id=file_version_id,
            )

            # --- imports → files / units ---
            for imp in list_imports_for_file_version(conn, file_version_id):
                target_file_id = _resolve_import_file(index, source_path, imp.module_specifier)
                if target_file_id is None:
                    continue
                stats["imports_resolved_to_files"] += 1
                insert_resolved_relationship(
                    conn,
                    snapshot_id=snapshot_id,
                    source_file_version_id=file_version_id,
                    source_unit_version_id=imp.source_unit_version_id,
                    relation_kind=REL_IMPORTS,
                    confidence=tier_for_precedence(PRECEDENCE_EXACT_PATH),
                    resolution_method="relative_import_path",
                    target_file_id=target_file_id,
                    start_line=imp.start_line,
                    end_line=imp.end_line,
                    metadata={"module_specifier": imp.module_specifier},
                )
                stats["relationships_added"] += 1

                imported = imp.imported_name or imp.local_alias
                resolved_unit_id: int | None = None
                if imported:
                    units = _exported_units_named(index, target_file_id, imported)
                    if len(units) == 1:
                        unit = units[0]
                        resolved_unit_id = unit.unit_id
                        insert_resolved_relationship(
                            conn,
                            snapshot_id=snapshot_id,
                            source_file_version_id=file_version_id,
                            source_unit_version_id=imp.source_unit_version_id,
                            relation_kind=REL_IMPORTS,
                            confidence=tier_for_precedence(PRECEDENCE_IMPORTED_ALIAS),
                            resolution_method="exported_name",
                            target_file_id=target_file_id,
                            target_unit_id=unit.unit_id,
                            start_line=imp.start_line,
                            end_line=imp.end_line,
                            metadata={
                                "module_specifier": imp.module_specifier,
                                "imported_name": imported,
                            },
                        )
                        stats["relationships_added"] += 1
                        stats["imported_names_resolved"] += 1
                    elif len(units) > 1:
                        # Persist all; none preferred (ambiguous export match).
                        for unit in units[:MAX_REFERENCE_CANDIDATES]:
                            insert_resolved_relationship(
                                conn,
                                snapshot_id=snapshot_id,
                                source_file_version_id=file_version_id,
                                source_unit_version_id=imp.source_unit_version_id,
                                relation_kind=REL_IMPORTS,
                                confidence=tier_for_precedence(PRECEDENCE_IMPORTED_ALIAS),
                                resolution_method="exported_name_ambiguous",
                                target_file_id=target_file_id,
                                target_unit_id=unit.unit_id,
                                start_line=imp.start_line,
                                end_line=imp.end_line,
                                metadata={
                                    "module_specifier": imp.module_specifier,
                                    "imported_name": imported,
                                },
                            )
                            stats["relationships_added"] += 1
                        stats["skipped_ambiguous"] += 1

                if source_is_test:
                    target_path = index.file_id_to_path.get(target_file_id, "")
                    if _is_production_path(target_path):
                        explicit_test_targets[target_file_id] = _prefer_explicit_test_evidence(
                            explicit_test_targets.get(target_file_id),
                            _ExplicitTestEvidence(
                                target_file_id=target_file_id,
                                confidence=tier_for_precedence(PRECEDENCE_EXACT_PATH),
                                resolution_method="test_import",
                                target_unit_id=resolved_unit_id,
                                source_unit_version_id=imp.source_unit_version_id,
                                start_line=imp.start_line,
                                end_line=imp.end_line,
                                metadata={
                                    "module_specifier": imp.module_specifier,
                                    "evidence": "import",
                                },
                            ),
                        )

            # --- references → candidate units ---
            for ref in list_references_for_file_version(conn, file_version_id):
                if _should_skip_identifier(ref.identifier):
                    continue
                candidates: list[_Candidate] = []

                # Precedence 2: imported alias resolved through a known export.
                for info in alias_map.get(ref.identifier, []):
                    if info.file_id == file_id:
                        continue
                    candidates.append(
                        _Candidate(
                            info=info,
                            precedence=PRECEDENCE_IMPORTED_ALIAS,
                            method="imported_alias",
                        )
                    )

                # Precedence 1: exact qualified name.
                for info in index.by_qualified.get(ref.identifier, []):
                    if info.file_id == file_id:
                        continue
                    candidates.append(
                        _Candidate(
                            info=info,
                            precedence=PRECEDENCE_EXACT_PATH,
                            method="qualified_name",
                        )
                    )

                # Precedence 3: unique exported unqualified name.
                exported = [
                    u
                    for u in index.by_exported_unqualified.get(ref.identifier, [])
                    if u.file_id != file_id
                ]
                if len(exported) == 1:
                    candidates.append(
                        _Candidate(
                            info=exported[0],
                            precedence=PRECEDENCE_UNIQUE_UNQUALIFIED,
                            method="unique_exported_name",
                        )
                    )
                elif 1 < len(exported) <= MAX_UNQUALIFIED_LOOKUP:
                    for info in exported[:MAX_REFERENCE_CANDIDATES]:
                        candidates.append(
                            _Candidate(
                                info=info,
                                precedence=PRECEDENCE_UNIQUE_UNQUALIFIED,
                                method="exported_name_ambiguous",
                            )
                        )
                    stats["skipped_ambiguous"] += 1
                elif len(exported) > MAX_UNQUALIFIED_LOOKUP:
                    stats["skipped_ambiguous"] += 1

                # Precedence 3: unique unqualified within package directory.
                if not any(c.precedence <= PRECEDENCE_UNIQUE_UNQUALIFIED for c in candidates):
                    source_pkg = PurePosixPath(source_path).parent.as_posix()
                    same_pkg = [
                        u
                        for u in index.by_unqualified.get(ref.identifier, [])
                        if u.file_id != file_id
                        and PurePosixPath(u.path).parent.as_posix() == source_pkg
                    ]
                    if len(same_pkg) == 1:
                        candidates.append(
                            _Candidate(
                                info=same_pkg[0],
                                precedence=PRECEDENCE_UNIQUE_UNQUALIFIED,
                                method="package_unqualified_name",
                            )
                        )
                    elif len(same_pkg) > 1:
                        for info in same_pkg[:MAX_REFERENCE_CANDIDATES]:
                            candidates.append(
                                _Candidate(
                                    info=info,
                                    precedence=PRECEDENCE_UNIQUE_UNQUALIFIED,
                                    method="package_unqualified_ambiguous",
                                )
                            )
                        stats["skipped_ambiguous"] += 1

                # Precedence 4: framework selector / component tag.
                # Fires for component_tag references and any identifier that
                # matches a known @Component/@Directive selector. Imported
                # component aliases already scored at rank 2; this covers
                # kebab-case tags and Angular selectors without an import hit.
                if not any(c.precedence < PRECEDENCE_FRAMEWORK_SELECTOR for c in candidates):
                    selector_hits = [
                        u
                        for u in index.by_selector.get(ref.identifier, [])
                        if u.file_id != file_id and u.semantic_role in {"component", "directive"}
                    ]
                    if not selector_hits and ref.reference_kind == "component_tag":
                        # PascalCase / imported tag name → component role unit.
                        selector_hits = [
                            u
                            for u in index.by_exported_unqualified.get(ref.identifier, [])
                            if u.file_id != file_id
                            and u.semantic_role in {"component", "directive"}
                        ]
                        if not selector_hits:
                            selector_hits = [
                                u
                                for u in index.by_unqualified.get(ref.identifier, [])
                                if u.file_id != file_id
                                and u.semantic_role in {"component", "directive"}
                            ]
                    if len(selector_hits) == 1:
                        candidates.append(
                            _Candidate(
                                info=selector_hits[0],
                                precedence=PRECEDENCE_FRAMEWORK_SELECTOR,
                                method="framework_selector",
                            )
                        )
                    elif 1 < len(selector_hits) <= MAX_UNQUALIFIED_LOOKUP:
                        for info in selector_hits[:MAX_REFERENCE_CANDIDATES]:
                            candidates.append(
                                _Candidate(
                                    info=info,
                                    precedence=PRECEDENCE_FRAMEWORK_SELECTOR,
                                    method="framework_selector_ambiguous",
                                )
                            )
                        stats["skipped_ambiguous"] += 1
                    elif len(selector_hits) > MAX_UNQUALIFIED_LOOKUP:
                        stats["skipped_ambiguous"] += 1

                ranked = _dedupe_candidates(candidates)
                # Lower-precedence never overrides a higher exact match: after
                # dedupe, drop anything weaker than the best survivor's tier
                # only when an exact match exists — keep all at that best rank
                # (and weaker only if no exact? Spec: persist all plausible;
                # lower never *overrides*. Keep all after dedupe by unit.)
                if ranked and ranked[0].confidence == CONFIDENCE_EXACT:
                    best_rank = ranked[0].precedence
                    # Keep exact-tier survivors; drop weaker-tier duplicates of
                    # other units only when they would confuse preferred picks.
                    # Spec: persist all plausible targets. Keep full ranked set.
                    _ = best_rank  # precedence already enforced by _dedupe
                _write_reference_targets(
                    conn,
                    snapshot_id=snapshot_id,
                    reference_id=ref.reference_id,
                    candidates=ranked,
                    stats=stats,
                )

                # Snapshot-scoped renders_component for resolved component tags.
                # Emit only when the target is a component/directive — never for
                # arbitrary symbols that merely share a name.
                component_targets = [
                    c for c in ranked if c.info.semantic_role in {"component", "directive"}
                ]
                if component_targets and (
                    ref.reference_kind == "component_tag"
                    or any(c.precedence == PRECEDENCE_FRAMEWORK_SELECTOR for c in ranked)
                ):
                    source_unit_version_id = ref.source_unit_version_id
                    # Prefer extractor tag (kebab-case) over normalized identifier.
                    edge_tag = _component_tag_label(ref)
                    for cand in component_targets[:MAX_REFERENCE_CANDIDATES]:
                        insert_resolved_relationship(
                            conn,
                            snapshot_id=snapshot_id,
                            source_file_version_id=file_version_id,
                            source_unit_version_id=source_unit_version_id,
                            relation_kind=REL_RENDERS_COMPONENT,
                            confidence=tier_for_precedence(PRECEDENCE_FRAMEWORK_SELECTOR),
                            resolution_method="framework_selector",
                            target_file_id=cand.info.file_id,
                            target_unit_id=cand.info.unit_id,
                            start_line=ref.start_line,
                            end_line=ref.end_line,
                            metadata={
                                "tag": edge_tag,
                                "via_precedence": cand.precedence,
                            },
                        )
                        stats["relationships_added"] += 1
                        if source_is_test and _is_production_path(cand.info.path):
                            explicit_test_targets[cand.info.file_id] = (
                                _prefer_explicit_test_evidence(
                                    explicit_test_targets.get(cand.info.file_id),
                                    _ExplicitTestEvidence(
                                        target_file_id=cand.info.file_id,
                                        confidence=tier_for_precedence(
                                            PRECEDENCE_FRAMEWORK_SELECTOR
                                        ),
                                        resolution_method="test_render",
                                        target_unit_id=cand.info.unit_id,
                                        source_unit_version_id=source_unit_version_id,
                                        start_line=ref.start_line,
                                        end_line=ref.end_line,
                                        metadata={
                                            "tag": edge_tag,
                                            "evidence": "renders_component",
                                        },
                                    ),
                                )
                            )

                # Call of a production unit from a test file is explicit evidence.
                if source_is_test and ref.reference_kind == "call" and ranked:
                    for cand in ranked[:MAX_REFERENCE_CANDIDATES]:
                        if not _is_production_path(cand.info.path):
                            continue
                        explicit_test_targets[cand.info.file_id] = _prefer_explicit_test_evidence(
                            explicit_test_targets.get(cand.info.file_id),
                            _ExplicitTestEvidence(
                                target_file_id=cand.info.file_id,
                                confidence=cand.confidence,
                                resolution_method="test_call",
                                target_unit_id=cand.info.unit_id,
                                source_unit_version_id=ref.source_unit_version_id,
                                start_line=ref.start_line,
                                end_line=ref.end_line,
                                metadata={
                                    "identifier": ref.identifier,
                                    "evidence": "call",
                                    "via_method": cand.method,
                                },
                            ),
                        )

            # --- resource links ---
            template_targets: list[tuple[int | None, int]] = []
            for link in list_resource_links_for_file_version(conn, file_version_id):
                if link.target_file_id is not None:
                    if link.resource_kind == "template":
                        template_targets.append((link.source_unit_version_id, link.target_file_id))
                    continue
                if not link.unresolved_path:
                    continue
                target_file_id = _resolve_import_file(index, source_path, link.unresolved_path)
                if target_file_id is None:
                    try:
                        normalized = normalize_relative_path(link.unresolved_path)
                    except ValueError:
                        continue
                    target_file_id = index.path_to_file_id.get(normalized)
                if target_file_id is None:
                    continue
                insert_resolved_relationship(
                    conn,
                    snapshot_id=snapshot_id,
                    source_file_version_id=file_version_id,
                    source_unit_version_id=link.source_unit_version_id,
                    relation_kind=f"resource:{link.resource_kind}",
                    confidence=tier_for_precedence(PRECEDENCE_EXACT_PATH),
                    resolution_method="resource_path",
                    target_file_id=target_file_id,
                    start_line=link.start_line,
                    end_line=link.end_line,
                    metadata={"unresolved_path": link.unresolved_path},
                )
                stats["relationships_added"] += 1
                stats["resource_links_resolved"] += 1
                if link.resource_kind == "template":
                    template_targets.append((link.source_unit_version_id, target_file_id))

            for source_unit_version_id, template_file_id in template_targets:
                _resolve_template_selectors(
                    conn,
                    index=index,
                    snapshot_id=snapshot_id,
                    source_file_version_id=file_version_id,
                    source_unit_version_id=source_unit_version_id,
                    template_file_id=template_file_id,
                    stats=stats,
                )

            # --- test → production ---
            # Explicit evidence (import / call / render) outranks filename pairing.
            for evidence in explicit_test_targets.values():
                insert_resolved_relationship(
                    conn,
                    snapshot_id=snapshot_id,
                    source_file_version_id=file_version_id,
                    source_unit_version_id=evidence.source_unit_version_id,
                    relation_kind=REL_TESTS,
                    confidence=evidence.confidence,
                    resolution_method=evidence.resolution_method,
                    target_file_id=evidence.target_file_id,
                    target_unit_id=evidence.target_unit_id,
                    start_line=evidence.start_line,
                    end_line=evidence.end_line,
                    metadata=evidence.metadata,
                )
                stats["relationships_added"] += 1

            # Filename-only affinity remains weak and is skipped when explicit
            # evidence already covers that production file.
            if source_is_test:
                prod_stem = _production_stem_from_test(source_path)
                if prod_stem:
                    prod_paths = [
                        p
                        for p in index.stem_to_paths.get(prod_stem.lower(), [])
                        if p != source_path and _is_production_path(p)
                    ]
                    if len(prod_paths) == 1:
                        target_file_id = index.path_to_file_id[prod_paths[0]]
                        if target_file_id not in explicit_test_targets:
                            insert_resolved_relationship(
                                conn,
                                snapshot_id=snapshot_id,
                                source_file_version_id=file_version_id,
                                relation_kind=REL_TESTS,
                                confidence=tier_for_precedence(PRECEDENCE_FILENAME_HEURISTIC),
                                resolution_method="test_filename_heuristic",
                                target_file_id=target_file_id,
                                metadata={"production_path": prod_paths[0]},
                            )
                            stats["relationships_added"] += 1
                    elif len(prod_paths) > 1:
                        stats["skipped_ambiguous"] += 1
    except BaseException:
        if owns_transaction:
            conn.execute("ROLLBACK")
        raise
    else:
        if owns_transaction:
            conn.execute("COMMIT")

    return ResolutionSummary(
        imports_resolved_to_files=stats["imports_resolved_to_files"],
        imported_names_resolved=stats["imported_names_resolved"],
        reference_targets_written=stats["reference_targets_written"],
        relationships_added=stats["relationships_added"],
        resource_links_resolved=stats["resource_links_resolved"],
        skipped_ambiguous=stats["skipped_ambiguous"],
    )


__all__ = ["resolve_snapshot"]
