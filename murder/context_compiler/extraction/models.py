"""Normalized immutable extraction contracts.

Language extractors return these records only. They must not open SQLite, mutate
snapshots, call LLMs, or depend on Murder runtime agents. Cross-file resolution
and persistence mapping happen later (coordinator / resolver / indexing mapper).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

ParseStatus = Literal["parsed", "partial", "text_only", "unsupported", "failed"]

DiagnosticSeverity = Literal["error", "warning", "info"]

# Extensible string aliases — not closed enums, so extractors can add kinds.
ImportKind = str
ReferenceKind = str
RelationKind = str
ResourceKind = str

# Suggested import kinds (not exhaustive).
IMPORT_MODULE = "module"
IMPORT_NAMED = "named"
IMPORT_DEFAULT = "default"
IMPORT_NAMESPACE = "namespace"
IMPORT_SIDE_EFFECT = "side_effect"
IMPORT_DYNAMIC = "dynamic"
IMPORT_TYPE_ONLY = "type_only"
IMPORT_RESOURCE = "resource"

# Suggested relation kinds (not exhaustive).
REL_CONTAINS = "contains"
REL_IMPORTS = "imports"
REL_EXPORTS = "exports"
REL_REFERENCES = "references"
REL_CALLS = "calls"
REL_INHERITS = "inherits"
REL_IMPLEMENTS = "implements"
REL_RENDERS_COMPONENT = "renders_component"
REL_TEMPLATE_OF = "template_of"
REL_STYLE_OF = "style_of"
REL_TESTS = "tests"
REL_CONFIGURED_BY = "configured_by"

# Suggested resource kinds (not exhaustive).
RESOURCE_TEMPLATE = "template"
RESOURCE_STYLE = "style"
RESOURCE_ASSET = "asset"
RESOURCE_GENERATED = "generated"


@dataclass(frozen=True, slots=True)
class ExtractionDiagnostic:
    """Observability record for extraction. Not recipient-facing prose."""

    severity: DiagnosticSeverity
    message: str
    backend: str
    start_line: int | None = None
    end_line: int | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedSemanticUnit:
    """One retrieval-worthy declaration within a file.

    ``local_id`` is file-local identity used to connect imports, references, and
    relationships before database IDs exist. It must be deterministic for
    identical source and extractor version — do not use line number as the sole
    logical identity.

    ``language_kind`` is syntactic (function, class, file, …).
    ``semantic_role`` is framework/domain role (component, hook, service, …).
    """

    local_id: str
    language_kind: str
    qualified_name: str
    unqualified_name: str
    start_line: int
    end_line: int
    semantic_role: str | None = None
    signature: str | None = None
    parent_local_id: str | None = None
    exported: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedImport:
    source_unit_local_id: str | None
    module_specifier: str
    import_kind: ImportKind
    start_line: int
    end_line: int
    imported_name: str | None = None
    local_alias: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedReference:
    """A name use that may remain unresolved or ambiguous."""

    source_unit_local_id: str | None
    identifier: str
    reference_kind: ReferenceKind
    start_line: int
    end_line: int
    candidate_local_ids: tuple[str, ...] = ()
    candidate_qualified_names: tuple[str, ...] = ()
    resolution_method: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedRelationship:
    source_unit_local_id: str | None
    relation_kind: RelationKind
    confidence: float
    resolution_method: str
    target_local_id: str | None = None
    target_qualified_name: str | None = None
    target_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExtractedResourceLink:
    source_unit_local_id: str
    target_path: str
    resource_kind: ResourceKind
    start_line: int | None = None
    end_line: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FileExtraction:
    """Primary extractor result for one source file."""

    path: str
    language: str
    parse_status: ParseStatus
    semantic_units: tuple[ExtractedSemanticUnit, ...] = ()
    imports: tuple[ExtractedImport, ...] = ()
    references: tuple[ExtractedReference, ...] = ()
    relationships: tuple[ExtractedRelationship, ...] = ()
    resource_links: tuple[ExtractedResourceLink, ...] = ()
    diagnostics: tuple[ExtractionDiagnostic, ...] = ()


__all__ = [
    "IMPORT_DEFAULT",
    "IMPORT_DYNAMIC",
    "IMPORT_MODULE",
    "IMPORT_NAMED",
    "IMPORT_NAMESPACE",
    "IMPORT_RESOURCE",
    "IMPORT_SIDE_EFFECT",
    "IMPORT_TYPE_ONLY",
    "REL_CALLS",
    "REL_CONFIGURED_BY",
    "REL_CONTAINS",
    "REL_EXPORTS",
    "REL_IMPLEMENTS",
    "REL_IMPORTS",
    "REL_INHERITS",
    "REL_REFERENCES",
    "REL_RENDERS_COMPONENT",
    "REL_STYLE_OF",
    "REL_TEMPLATE_OF",
    "REL_TESTS",
    "RESOURCE_ASSET",
    "RESOURCE_GENERATED",
    "RESOURCE_STYLE",
    "RESOURCE_TEMPLATE",
    "DiagnosticSeverity",
    "ExtractedImport",
    "ExtractedReference",
    "ExtractedRelationship",
    "ExtractedResourceLink",
    "ExtractedSemanticUnit",
    "ExtractionDiagnostic",
    "FileExtraction",
    "ImportKind",
    "ParseStatus",
    "ReferenceKind",
    "RelationKind",
    "ResourceKind",
]
