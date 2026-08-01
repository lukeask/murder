"""Map normalized extraction models onto persistence replacement payloads.

The mapper does not open SQLite or resolve cross-file targets. Callers may
optionally supply already-resolved local_id → logical_key maps and DB ids;
unresolved relationship / resource targets remain as names or paths for a
later resolver pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from murder.context_compiler.extraction.common import (
    disambiguator_from_metadata,
    make_logical_key,
)
from murder.context_compiler.extraction.models import (
    REL_RENDERS_COMPONENT,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.persistence.files import normalize_relative_path
from murder.context_compiler.persistence.records import (
    FileExtractionReplacement,
    ImportInput,
    ReferenceInput,
    ReferenceTargetInput,
    RelationshipInput,
    ResourceLinkInput,
    SemanticUnitVersionInput,
)


def logical_key_for_unit(
    unit: ExtractedSemanticUnit,
    *,
    language: str,
    path: str,
    local_id_to_logical_key: Mapping[str, str] | None = None,
) -> str:
    """Resolve the persistence logical key for one extracted unit."""
    if local_id_to_logical_key and unit.local_id in local_id_to_logical_key:
        return local_id_to_logical_key[unit.local_id]
    return make_logical_key(
        language=language,
        path=path,
        qualified_name=unit.qualified_name,
        language_kind=unit.language_kind,
        disambiguator=disambiguator_from_metadata(unit.metadata),
    )


def build_local_id_to_logical_key(
    extraction: FileExtraction,
    *,
    path: str | None = None,
    language: str | None = None,
    local_id_to_logical_key: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Map every unit ``local_id`` to its persistence logical key."""
    relative_path = normalize_relative_path(path or extraction.path)
    lang = language or extraction.language
    mapping: dict[str, str] = {}
    if local_id_to_logical_key:
        mapping.update(local_id_to_logical_key)
    for unit in extraction.semantic_units:
        if unit.local_id not in mapping:
            mapping[unit.local_id] = logical_key_for_unit(
                unit,
                language=lang,
                path=relative_path,
                local_id_to_logical_key=local_id_to_logical_key,
            )
    return mapping


def map_file_extraction(  # noqa: PLR0912, PLR0915
    extraction: FileExtraction,
    *,
    source_hash: str,
    byte_count: int,
    line_count: int,
    extractor_version: str,
    relative_path: str | None = None,
    language: str | None = None,
    parse_error: str | None = None,
    local_id_to_logical_key: Mapping[str, str] | None = None,
    resolved_unit_ids: Mapping[str, int] | None = None,
    resolved_file_ids: Mapping[str, int] | None = None,
    reference_target_ids: Mapping[str, tuple[ReferenceTargetInput, ...]] | None = None,
) -> FileExtractionReplacement:
    """Convert a :class:`FileExtraction` into a :class:`FileExtractionReplacement`.

    Parameters
    ----------
    extraction:
        Normalized extractor output for one file.
    source_hash, byte_count, line_count, extractor_version:
        File-version identity fields required by persistence (not known to
        extractors alone).
    relative_path:
        Worktree-relative path override; defaults to ``extraction.path``.
    language:
        Language override; defaults to ``extraction.language``.
    parse_error:
        Optional parse error string for failed/partial extractions.
    local_id_to_logical_key:
        Optional precomputed or overridden logical keys keyed by extraction
        ``local_id``. Missing entries are derived as
        ``language:path:qualified_name:language_kind[:disambiguator]``.
    resolved_unit_ids:
        Optional map of logical key *or* local_id → DB ``unit_id`` for
        relationship targets. Unresolved targets leave ``target_unit_id`` as
        ``None`` and retain qualified name / path in metadata.
    resolved_file_ids:
        Optional map of relative path → DB ``file_id`` for relationship and
        resource-link targets.
    reference_target_ids:
        Optional map of a reference identity key → already-resolved
        :class:`ReferenceTargetInput` rows. The identity key is
        ``f"{start_line}:{end_line}:{identifier}"``. When omitted, references
        are persisted without DB targets (candidates remain in metadata).
    """
    path = normalize_relative_path(relative_path or extraction.path)
    lang = language if language is not None else extraction.language
    id_map = build_local_id_to_logical_key(
        extraction,
        path=path,
        language=lang,
        local_id_to_logical_key=local_id_to_logical_key,
    )
    unit_ids = dict(resolved_unit_ids or {})
    file_ids = dict(resolved_file_ids or {})
    ref_targets = dict(reference_target_ids or {})

    def resolve_unit_id(token: str | None) -> int | None:
        if token is None:
            return None
        if token in unit_ids:
            return unit_ids[token]
        logical = id_map.get(token)
        if logical is not None and logical in unit_ids:
            return unit_ids[logical]
        return None

    def resolve_file_id(path_token: str | None) -> int | None:
        if path_token is None:
            return None
        if path_token in file_ids:
            return file_ids[path_token]
        try:
            normalized = normalize_relative_path(path_token)
        except ValueError:
            return None
        return file_ids.get(normalized)

    units: list[SemanticUnitVersionInput] = []
    for unit in extraction.semantic_units:
        logical_key = id_map[unit.local_id]
        parent_key: str | None = None
        if unit.parent_local_id is not None:
            parent_key = id_map.get(unit.parent_local_id)
            if parent_key is None:
                parent_key = unit.parent_local_id
        units.append(
            SemanticUnitVersionInput(
                logical_key=logical_key,
                language_kind=unit.language_kind,
                qualified_name=unit.qualified_name,
                unqualified_name=unit.unqualified_name,
                start_line=unit.start_line,
                end_line=unit.end_line,
                semantic_role=unit.semantic_role,
                signature=unit.signature,
                parent_logical_key=parent_key,
                exported=unit.exported,
                metadata=_mapping_to_dict(unit.metadata),
            )
        )

    imports = tuple(
        ImportInput(
            module_specifier=item.module_specifier,
            import_kind=item.import_kind,
            start_line=item.start_line,
            end_line=item.end_line,
            imported_name=item.imported_name,
            local_alias=item.local_alias,
            source_unit_logical_key=(
                id_map.get(item.source_unit_local_id) if item.source_unit_local_id else None
            ),
            metadata=_mapping_to_dict(item.metadata),
        )
        for item in extraction.imports
    )

    references: list[ReferenceInput] = []
    for ref in extraction.references:
        identity = f"{ref.start_line}:{ref.end_line}:{ref.identifier}"
        targets = ref_targets.get(identity, ())
        meta = _mapping_to_dict(ref.metadata) or {}
        if ref.candidate_local_ids:
            meta.setdefault(
                "candidate_local_ids",
                list(ref.candidate_local_ids),
            )
        if ref.candidate_qualified_names:
            meta.setdefault(
                "candidate_qualified_names",
                list(ref.candidate_qualified_names),
            )
        # Preserve unresolved candidate logical keys when mappable.
        candidate_keys = [id_map[cid] for cid in ref.candidate_local_ids if cid in id_map]
        if candidate_keys:
            meta.setdefault("candidate_logical_keys", candidate_keys)
        ambiguity = len(ref.candidate_local_ids) or len(ref.candidate_qualified_names)
        if targets:
            ambiguity = max(ambiguity, len(targets))
        references.append(
            ReferenceInput(
                identifier=ref.identifier,
                reference_kind=ref.reference_kind,
                start_line=ref.start_line,
                end_line=ref.end_line,
                source_unit_logical_key=(
                    id_map.get(ref.source_unit_local_id) if ref.source_unit_local_id else None
                ),
                resolution_method=ref.resolution_method,
                ambiguity_count=ambiguity,
                targets=targets,
                metadata=meta or None,
            )
        )

    # Same-file qualified-name → logical key for export/call edges that omit
    # target_local_id but name a unit extracted from this file.
    qual_to_logical: dict[str, str] = {}
    for unit in extraction.semantic_units:
        qual_to_logical.setdefault(unit.qualified_name, id_map[unit.local_id])

    relationships = _map_relationships(
        extraction.relationships,
        id_map=id_map,
        qual_to_logical=qual_to_logical,
        resolve_unit_id=resolve_unit_id,
        resolve_file_id=resolve_file_id,
    )

    # Cross-file renders_component edges cannot land in local relationships
    # (no concrete target yet). Promote them to component_tag references so
    # the snapshot resolver can bind them under PRECEDENCE_FRAMEWORK_SELECTOR.
    existing_ref_keys = {(ref.identifier, ref.start_line, ref.end_line) for ref in references}
    for rel in relationships:
        if rel.relation_kind != REL_RENDERS_COMPONENT:
            continue
        if rel.target_unit_id is not None or rel.target_file_id is not None:
            continue
        meta = rel.metadata or {}
        name = meta.get("target_qualified_name") or meta.get("tag") or meta.get("selector")
        if not isinstance(name, str) or not name:
            continue
        start = rel.start_line if rel.start_line is not None else 1
        end = rel.end_line if rel.end_line is not None else start
        key = (name, start, end)
        if key in existing_ref_keys:
            continue
        existing_ref_keys.add(key)
        ref_meta = dict(meta)
        ref_meta.setdefault("promoted_from", REL_RENDERS_COMPONENT)
        references.append(
            ReferenceInput(
                identifier=name,
                reference_kind="component_tag",
                start_line=start,
                end_line=end,
                source_unit_logical_key=rel.source_unit_logical_key,
                resolution_method=rel.resolution_method,
                ambiguity_count=0,
                targets=(),
                metadata=ref_meta,
            )
        )

    resource_links = tuple(
        ResourceLinkInput(
            resource_kind=link.resource_kind,
            source_unit_logical_key=id_map.get(
                link.source_unit_local_id, link.source_unit_local_id
            ),
            target_file_id=resolve_file_id(link.target_path),
            unresolved_path=(
                link.target_path if resolve_file_id(link.target_path) is None else None
            ),
            start_line=link.start_line,
            end_line=link.end_line,
            metadata=_mapping_to_dict(link.metadata),
        )
        for link in extraction.resource_links
    )

    return FileExtractionReplacement(
        relative_path=path,
        source_hash=source_hash,
        byte_count=byte_count,
        line_count=line_count,
        parse_status=extraction.parse_status,
        extractor_version=extractor_version,
        language=lang or None,
        parse_error=parse_error,
        units=tuple(units),
        imports=imports,
        references=tuple(references),
        relationships=tuple(relationships),
        resource_links=resource_links,
    )


def _map_relationships(
    relationships: Sequence[ExtractedRelationship],
    *,
    id_map: Mapping[str, str],
    qual_to_logical: Mapping[str, str],
    resolve_unit_id: Callable[[str | None], int | None],
    resolve_file_id: Callable[[str | None], int | None],
) -> list[RelationshipInput]:
    """Map extracted relationships, stashing same-file targets as logical keys."""
    mapped: list[RelationshipInput] = []
    for rel in relationships:
        meta = _mapping_to_dict(rel.metadata) or {}
        if rel.target_qualified_name:
            meta.setdefault("target_qualified_name", rel.target_qualified_name)
        if rel.target_path:
            meta.setdefault("target_path", rel.target_path)
        if rel.target_local_id and rel.target_local_id in id_map:
            meta.setdefault("target_logical_key", id_map[rel.target_local_id])
        elif rel.target_local_id:
            meta.setdefault("target_local_id", rel.target_local_id)
        elif (
            rel.target_qualified_name
            and rel.target_qualified_name in qual_to_logical
            and "target_logical_key" not in meta
        ):
            meta["target_logical_key"] = qual_to_logical[rel.target_qualified_name]

        target_unit_id = resolve_unit_id(rel.target_local_id)
        if target_unit_id is None and rel.target_qualified_name:
            target_unit_id = resolve_unit_id(rel.target_qualified_name)
        target_file_id = resolve_file_id(rel.target_path)

        mapped.append(
            RelationshipInput(
                relation_kind=rel.relation_kind,
                confidence=rel.confidence,
                resolution_method=rel.resolution_method,
                source_unit_logical_key=(
                    id_map.get(rel.source_unit_local_id) if rel.source_unit_local_id else None
                ),
                target_file_id=target_file_id,
                target_unit_id=target_unit_id,
                start_line=rel.start_line,
                end_line=rel.end_line,
                metadata=meta or None,
            )
        )
    return mapped


def _mapping_to_dict(metadata: Mapping[str, object] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    return dict(metadata)


__all__ = [
    "build_local_id_to_logical_key",
    "logical_key_for_unit",
    "map_file_extraction",
]
