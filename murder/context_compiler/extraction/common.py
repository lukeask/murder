"""Shared helpers for deterministic extraction pipelines.

Conceptual phases (concrete extractors may combine internally)::

    parse
    → declare semantic units
    → extract imports
    → extract references
    → framework enrichment
    → local relationship derivation
    → normalize

Helpers here cover identity, naming, ranges, signatures, disambiguation,
export hints, parent containment, enclosing-range lookup, and diagnostics.
Avoid forcing a single syntax-traversal abstraction across languages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from murder.context_compiler.extraction.models import (
    REL_CONTAINS,
    DiagnosticSeverity,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    ExtractionDiagnostic,
    FileExtraction,
    ParseStatus,
)

# Normalized extraction schema version. Changing field semantics or logical-key
# construction requires bumping this so file-version reuse is invalidated.
EXTRACTION_SCHEMA_VERSION = "schema-1"

_WHITESPACE_RE = re.compile(r"\s+")


def make_local_id(
    *,
    language_kind: str,
    qualified_name: str,
    disambiguator: str | None = None,
) -> str:
    """Build a deterministic file-local semantic-unit id.

    Identity is ``language_kind:qualified_name[:disambiguator]``. Line numbers
    are intentionally excluded so edits that shift a declaration do not change
    its local identity when the qualified name is stable.
    """
    if not language_kind:
        raise ValueError("language_kind must be non-empty")
    if not qualified_name:
        raise ValueError("qualified_name must be non-empty")
    if disambiguator:
        return f"{language_kind}:{qualified_name}:{disambiguator}"
    return f"{language_kind}:{qualified_name}"


def make_logical_key(
    *,
    language: str,
    path: str,
    qualified_name: str,
    language_kind: str,
    disambiguator: str | None = None,
) -> str:
    """Build the persistence logical key for a semantic unit.

    Shape: ``language:path:qualified_name:language_kind[:disambiguator]``.
    """
    if not language:
        raise ValueError("language must be non-empty")
    if not path:
        raise ValueError("path must be non-empty")
    if not qualified_name:
        raise ValueError("qualified_name must be non-empty")
    if not language_kind:
        raise ValueError("language_kind must be non-empty")
    key = f"{language}:{path}:{qualified_name}:{language_kind}"
    if disambiguator:
        return f"{key}:{disambiguator}"
    return key


def build_qualified_name(*parts: str, separator: str = ".") -> str:
    """Join non-empty name parts into a qualified name."""
    cleaned = [p for p in parts if p]
    if not cleaned:
        raise ValueError("qualified name requires at least one non-empty part")
    return separator.join(cleaned)


def inclusive_range(start_line: int, end_line: int) -> tuple[int, int]:
    """Validate and return a one-based inclusive ``(start, end)`` line range."""
    if start_line <= 0 or end_line <= 0:
        raise ValueError(f"line range endpoints must be positive; got {start_line}-{end_line}")
    if end_line < start_line:
        raise ValueError(f"end_line ({end_line}) must be >= start_line ({start_line})")
    return start_line, end_line


def normalize_signature(signature: str | None) -> str | None:
    """Collapse interior whitespace in a signature; preserve ``None``."""
    if signature is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", signature.strip())
    return collapsed or None


def disambiguator_from_metadata(metadata: Mapping[str, object] | None) -> str | None:
    """Read an optional ``disambiguator`` string from unit metadata."""
    if not metadata:
        return None
    value = metadata.get("disambiguator")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def assign_disambiguators(
    units: Sequence[ExtractedSemanticUnit],
) -> tuple[ExtractedSemanticUnit, ...]:
    """Ensure colliding ``(language_kind, qualified_name)`` pairs get disambiguators.

    Existing metadata disambiguators are preserved. New collisions receive
    stable ordinal suffixes ``overload-1``, ``overload-2``, … ordered by
    ``(start_line, end_line, local_id)``.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for index, unit in enumerate(units):
        key = (unit.language_kind, unit.qualified_name)
        groups.setdefault(key, []).append(index)

    updates: dict[int, ExtractedSemanticUnit] = {}
    for indices in groups.values():
        if len(indices) < 2:
            continue
        ordered = sorted(
            indices,
            key=lambda i: (
                units[i].start_line,
                units[i].end_line,
                units[i].local_id,
            ),
        )
        for ordinal, index in enumerate(ordered, start=1):
            unit = units[index]
            existing = disambiguator_from_metadata(unit.metadata)
            disambiguator = existing or f"overload-{ordinal}"
            meta = dict(unit.metadata)
            meta["disambiguator"] = disambiguator
            new_local_id = make_local_id(
                language_kind=unit.language_kind,
                qualified_name=unit.qualified_name,
                disambiguator=disambiguator,
            )
            updates[index] = replace(unit, local_id=new_local_id, metadata=meta)

    if not updates:
        return tuple(units)
    return tuple(updates.get(i, unit) for i, unit in enumerate(units))


def is_public_python_name(name: str) -> bool:
    """Heuristic: Python names not starting with ``_`` are treated as public."""
    return bool(name) and not name.startswith("_")


def is_exported_hint(
    *,
    language: str,
    unqualified_name: str,
    explicit_exported: bool | None = None,
    has_export_keyword: bool = False,
) -> bool:
    """Best-effort exported/public detection shared across backends.

    Prefer an explicit extractor decision when provided. Otherwise:
    * JS/TS-family: ``has_export_keyword``;
    * Python: non-underscore names;
    * other languages: ``False`` unless explicitly marked.
    """
    if explicit_exported is not None:
        return explicit_exported
    if has_export_keyword:
        return True
    normalized = language.lower()
    if normalized in {"python", "py"}:
        return is_public_python_name(unqualified_name)
    return False


def unit_contains_line(unit: ExtractedSemanticUnit, line: int) -> bool:
    """Return whether ``line`` falls in the unit's inclusive range."""
    return unit.start_line <= line <= unit.end_line


def find_enclosing_unit(
    units: Sequence[ExtractedSemanticUnit],
    line: int,
    *,
    exclude_local_ids: Iterable[str] = (),
) -> ExtractedSemanticUnit | None:
    """Return the innermost unit whose inclusive range contains ``line``.

    When several units contain the line, prefer the tightest span, then the
    latest ``start_line``, then ``local_id`` for determinism.
    """
    excluded = frozenset(exclude_local_ids)
    candidates = [u for u in units if u.local_id not in excluded and unit_contains_line(u, line)]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda u: (u.end_line - u.start_line, -u.start_line, u.local_id),
    )


def derive_contains_relationships(
    units: Sequence[ExtractedSemanticUnit],
    *,
    confidence: float = 1.0,
    resolution_method: str = "parent_local_id",
) -> tuple[ExtractedRelationship, ...]:
    """Emit ``contains`` edges from ``parent_local_id`` links."""
    by_id = {u.local_id: u for u in units}
    edges: list[ExtractedRelationship] = []
    for unit in units:
        parent_id = unit.parent_local_id
        if parent_id is None:
            continue
        if parent_id not in by_id:
            continue
        edges.append(
            ExtractedRelationship(
                source_unit_local_id=parent_id,
                target_local_id=unit.local_id,
                target_qualified_name=unit.qualified_name,
                relation_kind=REL_CONTAINS,
                start_line=unit.start_line,
                end_line=unit.end_line,
                confidence=confidence,
                resolution_method=resolution_method,
            )
        )
    edges.sort(
        key=lambda e: (
            e.source_unit_local_id or "",
            e.target_local_id or "",
            e.start_line or 0,
            e.end_line or 0,
        )
    )
    return tuple(edges)


def assign_parents_by_enclosure(
    units: Sequence[ExtractedSemanticUnit],
) -> tuple[ExtractedSemanticUnit, ...]:
    """Fill missing ``parent_local_id`` using innermost enclosing ranges.

    Units that already set ``parent_local_id`` are left unchanged. A unit is
    never assigned itself as parent.
    """
    result: list[ExtractedSemanticUnit] = []
    for unit in units:
        if unit.parent_local_id is not None:
            result.append(unit)
            continue
        parent = find_enclosing_unit(
            units,
            unit.start_line,
            exclude_local_ids=(unit.local_id,),
        )
        # Prefer a parent that also covers end_line when possible.
        if parent is not None and not unit_contains_line(parent, unit.end_line):
            parent = find_enclosing_unit(
                [u for u in units if unit_contains_line(u, unit.end_line)],
                unit.start_line,
                exclude_local_ids=(unit.local_id,),
            )
        if parent is None:
            result.append(unit)
        else:
            result.append(replace(unit, parent_local_id=parent.local_id))
    return tuple(result)


@dataclass
class DiagnosticAccumulator:
    """Mutable collector that yields an immutable diagnostic tuple."""

    backend: str
    _items: list[ExtractionDiagnostic] = field(default_factory=list)

    def add(
        self,
        severity: DiagnosticSeverity,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        code: str | None = None,
        backend: str | None = None,
    ) -> None:
        self._items.append(
            ExtractionDiagnostic(
                severity=severity,
                message=message,
                backend=backend or self.backend,
                start_line=start_line,
                end_line=end_line,
                code=code,
            )
        )

    def error(
        self,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        code: str | None = None,
        backend: str | None = None,
    ) -> None:
        self.add(
            "error",
            message,
            start_line=start_line,
            end_line=end_line,
            code=code,
            backend=backend,
        )

    def warning(
        self,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        code: str | None = None,
        backend: str | None = None,
    ) -> None:
        self.add(
            "warning",
            message,
            start_line=start_line,
            end_line=end_line,
            code=code,
            backend=backend,
        )

    def info(
        self,
        message: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        code: str | None = None,
        backend: str | None = None,
    ) -> None:
        self.add(
            "info",
            message,
            start_line=start_line,
            end_line=end_line,
            code=code,
            backend=backend,
        )

    def extend(self, diagnostics: Sequence[ExtractionDiagnostic]) -> None:
        self._items.extend(diagnostics)

    def as_tuple(self) -> tuple[ExtractionDiagnostic, ...]:
        return tuple(self._items)


def build_extractor_version(
    schema_version: str,
    backend_version: str,
    *enricher_versions: str,
) -> str:
    """Compose ``schema-N:backend-V[:enricher-V…]`` version identity."""
    if not schema_version:
        raise ValueError("schema_version must be non-empty")
    if not backend_version:
        raise ValueError("backend_version must be non-empty")
    parts = [schema_version, backend_version, *[v for v in enricher_versions if v]]
    return ":".join(parts)


def empty_file_extraction(
    path: str,
    language: str,
    parse_status: ParseStatus,
    *,
    diagnostics: Sequence[ExtractionDiagnostic] = (),
) -> FileExtraction:
    """Convenience constructor for non-structural outcomes."""
    return FileExtraction(
        path=path,
        language=language,
        parse_status=parse_status,
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "EXTRACTION_SCHEMA_VERSION",
    "DiagnosticAccumulator",
    "assign_disambiguators",
    "assign_parents_by_enclosure",
    "build_extractor_version",
    "build_qualified_name",
    "derive_contains_relationships",
    "disambiguator_from_metadata",
    "empty_file_extraction",
    "find_enclosing_unit",
    "inclusive_range",
    "is_exported_hint",
    "is_public_python_name",
    "make_local_id",
    "make_logical_key",
    "normalize_signature",
    "unit_contains_line",
]
