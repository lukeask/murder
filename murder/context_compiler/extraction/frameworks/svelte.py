"""Svelte single-file component extractor.

``.svelte`` files get one file-scoped component aggregate plus best-effort
extraction of scripts, props, imported components, top-level functions,
snippets, styles, and Svelte 5 ``$props`` / ``{@render`` markers.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    derive_contains_relationships,
    make_local_id,
)
from murder.context_compiler.extraction.frameworks._sfc import (
    SfcBlock,
    component_name_from_path,
    pascal_to_kebab,
    shift_line,
    split_sfc_blocks,
)
from murder.context_compiler.extraction.models import (
    IMPORT_RESOURCE,
    REL_RENDERS_COMPONENT,
    REL_STYLE_OF,
    RESOURCE_ASSET,
    RESOURCE_STYLE,
    RESOURCE_TEMPLATE,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedResourceLink,
    ExtractedSemanticUnit,
    ExtractionDiagnostic,
    FileExtraction,
    ParseStatus,
)
from murder.context_compiler.extraction.registry import (
    LANG_SVELTE,
    ExtractorRegistry,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_JAVASCRIPT,
    GRAMMAR_TSX,
    GRAMMAR_TYPESCRIPT,
    grammar_status,
    parse_source,
)
from murder.context_compiler.extraction.treesitter.javascript import extract_tree

EXTRACTOR_ID = "svelte-sfc"
EXTRACTOR_VERSION = "svelte-sfc-1"

_MARKUP_COMPONENT_RE = re.compile(
    r"<(?P<name>[A-Z][A-Za-z0-9]*|[a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?=[\s/>])"
)
_SNIPPET_RE = re.compile(r"\{#snippet\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<params>\([^)]*\))?")
_RENDER_RE = re.compile(r"\{@render\s+(?P<expr>[^}]+)\}")
_PROPS_RE = re.compile(
    r"\blet\s+\{(?P<body>[^}]*)\}\s*=\s*\$props\s*\(|\b(?:const|let)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$props\s*\("
)
_EXPORT_LET_RE = re.compile(r"\bexport\s+let\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _line_count(source: str) -> int:
    if not source:
        return 1
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def _script_language(block: SfcBlock) -> tuple[str, str]:
    lang = (block.attrs.get("lang") or "").strip().lower()
    if lang in {"ts", "typescript"}:
        return "typescript", GRAMMAR_TYPESCRIPT
    if lang in {"tsx"}:
        return "tsx", GRAMMAR_TSX
    return "javascript", GRAMMAR_JAVASCRIPT


def _shift_extraction(
    extraction: FileExtraction,
    *,
    line_offset: int,
    parent_local_id: str,
    id_prefix: str,
) -> FileExtraction:
    id_map: dict[str, str] = {}
    units: list[ExtractedSemanticUnit] = []
    for unit in extraction.semantic_units:
        new_id = f"{id_prefix}/{unit.local_id}"
        id_map[unit.local_id] = new_id
        parent = (
            parent_local_id
            if unit.parent_local_id is None
            else id_map.get(unit.parent_local_id, f"{id_prefix}/{unit.parent_local_id}")
        )
        units.append(
            replace(
                unit,
                local_id=new_id,
                parent_local_id=parent,
                start_line=unit.start_line + line_offset,
                end_line=unit.end_line + line_offset,
            )
        )

    fixed_units: list[ExtractedSemanticUnit] = []
    for unit in units:
        resolved_parent: str | None = unit.parent_local_id
        if resolved_parent and resolved_parent.startswith(f"{id_prefix}/"):
            original = resolved_parent[len(id_prefix) + 1 :]
            if original in id_map:
                resolved_parent = id_map[original]
            fixed_units.append(replace(unit, parent_local_id=resolved_parent))
        else:
            fixed_units.append(unit)

    def remap(value: str | None) -> str | None:
        if value is None:
            return None
        return id_map.get(value, value)

    imports = tuple(
        replace(
            item,
            source_unit_local_id=remap(item.source_unit_local_id) or parent_local_id,
            start_line=item.start_line + line_offset,
            end_line=item.end_line + line_offset,
        )
        for item in extraction.imports
    )
    references = tuple(
        replace(
            item,
            source_unit_local_id=remap(item.source_unit_local_id) or parent_local_id,
            candidate_local_ids=tuple(remap(x) or x for x in item.candidate_local_ids),
            start_line=item.start_line + line_offset,
            end_line=item.end_line + line_offset,
        )
        for item in extraction.references
    )
    relationships = tuple(
        replace(
            item,
            source_unit_local_id=remap(item.source_unit_local_id) or parent_local_id,
            target_local_id=remap(item.target_local_id),
            start_line=shift_line(item.start_line, line_offset),
            end_line=shift_line(item.end_line, line_offset),
        )
        for item in extraction.relationships
    )
    return replace(
        extraction,
        semantic_units=tuple(fixed_units),
        imports=imports,
        references=references,
        relationships=relationships,
    )


def _imported_component_aliases(imports: tuple[ExtractedImport, ...]) -> dict[str, ExtractedImport]:
    out: dict[str, ExtractedImport] = {}
    for item in imports:
        alias = item.local_alias or item.imported_name
        if not alias:
            continue
        spec = item.module_specifier
        is_componentish = (
            spec.endswith(".svelte") or bool(re.match(r"^[A-Z]", alias)) or "/" in spec
        )
        if not is_componentish:
            continue
        out[alias] = item
        out[pascal_to_kebab(alias)] = item
    return out


def _resolve_src(path: str, src: str) -> str:
    base_dir = PurePosixPath(path.replace("\\", "/")).parent
    joined = (base_dir / src).as_posix()
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _markup_region(source: str) -> tuple[str, int]:
    """Return markup outside script/style blocks plus its starting line."""
    spans: list[tuple[int, int]] = []
    lower = source
    for name in ("script", "style"):
        open_re = re.compile(rf"<{name}\b[^>]*>", re.IGNORECASE)
        close_re = re.compile(rf"</{name}\s*>", re.IGNORECASE)
        search_pos = 0
        while True:
            m = open_re.search(lower, search_pos)
            if m is None:
                break
            if m.group(0).rstrip().endswith("/>"):
                search_pos = m.end()
                continue
            c = close_re.search(lower, m.end())
            end = c.end() if c else len(source)
            spans.append((m.start(), end))
            search_pos = end
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    parts: list[str] = []
    cursor = 0
    first_content_index: int | None = None
    for start, end in merged:
        if cursor < start:
            chunk = source[cursor:start]
            if chunk.strip() and first_content_index is None:
                first_content_index = cursor
            parts.append(chunk)
        cursor = end
    if cursor < len(source):
        chunk = source[cursor:]
        if chunk.strip() and first_content_index is None:
            first_content_index = cursor
        parts.append(chunk)
    markup = "".join(parts)
    start_line = (
        1 if first_content_index is None else source.count("\n", 0, first_content_index) + 1
    )
    return markup, start_line


class SvelteExtractor:
    """Language extractor for Svelte ``.svelte`` components."""

    extractor_id = EXTRACTOR_ID
    extractor_version = EXTRACTOR_VERSION

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        if _path_extension(path) == ".svelte":
            return True
        if language_hint and language_hint.strip().lower() in {"svelte"}:
            return True
        return False

    def extract(  # noqa: PLR0912, PLR0915
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        diagnostics = DiagnosticAccumulator(self.extractor_id)
        name = component_name_from_path(path)
        total_lines = max(_line_count(source), 1)
        component_id = make_local_id(language_kind="file", qualified_name=name)
        component_meta: dict[str, object] = {"framework": "svelte", "sfc": True}
        component = ExtractedSemanticUnit(
            local_id=component_id,
            language_kind="file",
            semantic_role="component",
            qualified_name=name,
            unqualified_name=name,
            start_line=1,
            end_line=total_lines,
            exported=True,
            metadata=component_meta,
        )

        blocks = split_sfc_blocks(source, tags=("script", "style"))
        script_blocks = [b for b in blocks if b.name == "script"]
        style_blocks = [b for b in blocks if b.name == "style"]

        units: list[ExtractedSemanticUnit] = [component]
        imports: list[ExtractedImport] = []
        references: list[ExtractedReference] = []
        relationships: list[ExtractedRelationship] = []
        resource_links: list[ExtractedResourceLink] = []
        extra_diags: list[ExtractionDiagnostic] = []
        parse_status: ParseStatus = "parsed"
        props: list[str] = []

        for index, block in enumerate(script_blocks):
            context = (
                "module"
                if block.attrs.get("context") == "module" or "module" in block.attrs
                else "instance"
            )
            src = block.attrs.get("src")
            if src:
                target = _resolve_src(path, src)
                resource_links.append(
                    ExtractedResourceLink(
                        source_unit_local_id=component_id,
                        target_path=target,
                        resource_kind=RESOURCE_ASSET,
                        start_line=block.open_line,
                        end_line=block.open_line,
                        metadata={"block": "script", "context": context, "framework": "svelte"},
                    )
                )
                imports.append(
                    ExtractedImport(
                        source_unit_local_id=component_id,
                        module_specifier=src,
                        import_kind=IMPORT_RESOURCE,
                        start_line=block.open_line,
                        end_line=block.open_line,
                        metadata={"external_src": True, "context": context},
                    )
                )
                continue

            language, grammar = _script_language(block)
            status = grammar_status(grammar)
            if not status.available and grammar == GRAMMAR_TYPESCRIPT:
                grammar = GRAMMAR_JAVASCRIPT
                language = "javascript"
                status = grammar_status(grammar)
            if not status.available:
                diagnostics.warning(
                    f"script grammar {grammar!r} unavailable",
                    start_line=block.open_line,
                    code="grammar_unavailable",
                )
                parse_status = "partial"
                continue

            try:
                tree = parse_source(grammar, block.content)
            except Exception as exc:
                diagnostics.warning(
                    f"script parse failed: {type(exc).__name__}: {exc}",
                    start_line=block.open_line,
                    code="script_parse_failed",
                )
                parse_status = "partial"
                continue
            if tree is None:
                parse_status = "partial"
                continue

            script_extraction = extract_tree(
                path=path,
                language=language,
                backend=f"{self.extractor_id}:script",
                tree=tree,
            )
            if script_extraction.parse_status in {"partial", "failed"}:
                parse_status = "partial"
            line_offset = block.content_start_line - 1
            shifted = _shift_extraction(
                script_extraction,
                line_offset=line_offset,
                parent_local_id=component_id,
                id_prefix=f"script{index}",
            )
            # Annotate script children with context.
            for unit in shifted.semantic_units:
                meta = dict(unit.metadata)
                meta["script_context"] = context
                units.append(replace(unit, metadata=meta))
            imports.extend(shifted.imports)
            references.extend(shifted.references)
            relationships.extend(shifted.relationships)
            extra_diags.extend(shifted.diagnostics)

            for match in _EXPORT_LET_RE.finditer(block.content):
                prop = match.group("name")
                props.append(prop)
                line = block.content_start_line + block.content[: match.start()].count("\n")
                references.append(
                    ExtractedReference(
                        source_unit_local_id=component_id,
                        identifier=prop,
                        reference_kind="prop",
                        start_line=line,
                        end_line=line,
                        resolution_method="svelte_export_let",
                        metadata={"framework": "svelte"},
                    )
                )

            for match in _PROPS_RE.finditer(block.content):
                component_meta["svelte5_props"] = True
                line = block.content_start_line + block.content[: match.start()].count("\n")
                body = match.group("body")
                if body:
                    for part in body.split(","):
                        token = part.strip().split(":")[0].strip().split("=")[0].strip()
                        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
                            props.append(token)
                named = match.group("name")
                if named:
                    props.append(named)
                references.append(
                    ExtractedReference(
                        source_unit_local_id=component_id,
                        identifier="$props",
                        reference_kind="macro",
                        start_line=line,
                        end_line=line,
                        resolution_method="svelte_props",
                        metadata={"framework": "svelte"},
                    )
                )

        if props:
            component_meta["props"] = tuple(dict.fromkeys(props))
            units[0] = replace(component, metadata=dict(component_meta))

        # Markup (everything outside script/style).
        markup, markup_start_line = _markup_region(source)
        resource_links.append(
            ExtractedResourceLink(
                source_unit_local_id=component_id,
                target_path=path,
                resource_kind=RESOURCE_TEMPLATE,
                start_line=markup_start_line,
                end_line=markup_start_line,
                metadata={"block": "markup", "inline": True, "framework": "svelte"},
            )
        )

        aliases = _imported_component_aliases(tuple(imports))
        for match in _MARKUP_COMPONENT_RE.finditer(markup):
            tag = match.group("name")
            rel_line = markup[: match.start()].count("\n")
            abs_line = markup_start_line + rel_line
            imported = aliases.get(tag)
            if imported is None and not re.match(r"^[A-Z]", tag):
                continue
            target_name = (
                (imported.local_alias or imported.imported_name or tag) if imported else tag
            )
            relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=component_id,
                    target_qualified_name=target_name,
                    target_path=imported.module_specifier if imported else None,
                    relation_kind=REL_RENDERS_COMPONENT,
                    start_line=abs_line,
                    end_line=abs_line,
                    confidence=0.8 if imported else 0.55,
                    resolution_method="svelte_markup_tag",
                    metadata={"tag": tag, "framework": "svelte"},
                )
            )

        for match in _SNIPPET_RE.finditer(markup):
            snippet_name = match.group("name")
            rel_line = markup[: match.start()].count("\n")
            abs_line = markup_start_line + rel_line
            # Approximate end: next snippet or end of markup.
            snippet_id = make_local_id(
                language_kind="snippet",
                qualified_name=f"{name}.{snippet_name}",
            )
            params = (match.group("params") or "()").strip()
            units.append(
                ExtractedSemanticUnit(
                    local_id=snippet_id,
                    language_kind="snippet",
                    semantic_role="snippet",
                    qualified_name=f"{name}.{snippet_name}",
                    unqualified_name=snippet_name,
                    start_line=abs_line,
                    end_line=abs_line,
                    parent_local_id=component_id,
                    signature=f"snippet {snippet_name}{params}",
                    metadata={"framework": "svelte"},
                )
            )

        for match in _RENDER_RE.finditer(markup):
            expr = match.group("expr").strip()
            rel_line = markup[: match.start()].count("\n")
            abs_line = markup_start_line + rel_line
            # {@render children(...)} → renders toward the snippet/identifier head.
            head = re.split(r"[\s(+.]", expr, maxsplit=1)[0]
            references.append(
                ExtractedReference(
                    source_unit_local_id=component_id,
                    identifier=head or expr,
                    reference_kind="render",
                    start_line=abs_line,
                    end_line=abs_line,
                    resolution_method="svelte_render",
                    metadata={"expression": expr, "framework": "svelte"},
                )
            )
            if head:
                relationships.append(
                    ExtractedRelationship(
                        source_unit_local_id=component_id,
                        target_qualified_name=head,
                        relation_kind=REL_RENDERS_COMPONENT,
                        start_line=abs_line,
                        end_line=abs_line,
                        confidence=0.5,
                        resolution_method="svelte_render",
                        metadata={"expression": expr, "framework": "svelte"},
                    )
                )

        for index, block in enumerate(style_blocks):
            src = block.attrs.get("src")
            if src:
                target = _resolve_src(path, src)
                resource_links.append(
                    ExtractedResourceLink(
                        source_unit_local_id=component_id,
                        target_path=target,
                        resource_kind=RESOURCE_STYLE,
                        start_line=block.open_line,
                        end_line=block.open_line,
                        metadata={"block": "style", "framework": "svelte"},
                    )
                )
                relationships.append(
                    ExtractedRelationship(
                        source_unit_local_id=component_id,
                        target_path=target,
                        relation_kind=REL_STYLE_OF,
                        start_line=block.open_line,
                        end_line=block.open_line,
                        confidence=1.0,
                        resolution_method="svelte_style_src",
                    )
                )
            else:
                resource_links.append(
                    ExtractedResourceLink(
                        source_unit_local_id=component_id,
                        target_path=path,
                        resource_kind=RESOURCE_STYLE,
                        start_line=block.start_line,
                        end_line=block.end_line,
                        metadata={
                            "block": "style",
                            "inline": True,
                            "framework": "svelte",
                            "index": index,
                            "lang": block.attrs.get("lang") or "css",
                        },
                    )
                )
                relationships.append(
                    ExtractedRelationship(
                        source_unit_local_id=component_id,
                        target_path=path,
                        relation_kind=REL_STYLE_OF,
                        start_line=block.start_line,
                        end_line=block.end_line,
                        confidence=1.0,
                        resolution_method="svelte_inline_style",
                    )
                )

        contains = derive_contains_relationships(units)
        relationships_out = tuple(
            sorted(
                (*relationships, *contains),
                key=lambda r: (
                    r.relation_kind,
                    r.source_unit_local_id or "",
                    r.target_local_id or "",
                    r.target_qualified_name or "",
                    r.target_path or "",
                    r.start_line or 0,
                ),
            )
        )

        return FileExtraction(
            path=path,
            language=LANG_SVELTE,
            parse_status=parse_status,
            semantic_units=tuple(units),
            imports=tuple(
                sorted(
                    imports,
                    key=lambda i: (
                        i.start_line,
                        i.import_kind,
                        i.module_specifier,
                        i.imported_name or "",
                    ),
                )
            ),
            references=tuple(
                sorted(
                    references,
                    key=lambda r: (r.start_line, r.reference_kind, r.identifier),
                )
            ),
            relationships=relationships_out,
            resource_links=tuple(
                sorted(
                    resource_links,
                    key=lambda r: (
                        r.resource_kind,
                        r.source_unit_local_id,
                        r.target_path,
                        r.start_line or 0,
                    ),
                )
            ),
            diagnostics=tuple([*diagnostics.as_tuple(), *extra_diags]),
        )


def register_svelte_extractor(registry: ExtractorRegistry) -> None:
    """Register the Svelte SFC extractor for ``.svelte`` files."""
    registry.register(
        SvelteExtractor(),
        languages=(LANG_SVELTE,),
        extensions=(".svelte",),
        priority=30,
    )


__all__ = [
    "EXTRACTOR_ID",
    "EXTRACTOR_VERSION",
    "SvelteExtractor",
    "register_svelte_extractor",
]
