"""Angular framework enricher over TypeScript base extractions.

Recognizes ``@Component`` / ``@Directive`` / ``@Pipe`` / ``@Injectable``
decorated classes, extracts decorator metadata, resource links for external
templates/styles, and best-effort ``renders_component`` edges from template
selectors.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath

from murder.context_compiler.extraction.models import (
    REL_IMPORTS,
    REL_RENDERS_COMPONENT,
    REL_STYLE_OF,
    REL_TEMPLATE_OF,
    RESOURCE_STYLE,
    RESOURCE_TEMPLATE,
    ExtractedRelationship,
    ExtractedResourceLink,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import (
    LANG_TSX,
    LANG_TYPESCRIPT,
    ExtractorRegistry,
)

ENRICHER_ID = "angular"
ENRICHER_VERSION = "angular-1"

_ANGULAR_LANGS = frozenset({LANG_TYPESCRIPT, LANG_TSX, "ts"})
_ANGULAR_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})

_DECORATOR_ROLES: dict[str, str] = {
    "Component": "component",
    "Directive": "directive",
    "Pipe": "pipe",
    "Injectable": "service",
}

_ANGULAR_IMPORT_RE = re.compile(r"""from\s+['"]@angular/[^'"]+['"]|require\s*\(\s*['"]@angular/""")
_DECORATOR_RE = re.compile(
    r"@(Component|Directive|Pipe|Injectable)\s*\(",
    re.MULTILINE,
)

_CLASS_AFTER_RE = re.compile(
    r"(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)

# Object-literal property extractors (best-effort, non-nested-aware enough for
# typical Angular decorator configs).
_STR_PROP_RE = re.compile(
    r"""(?P<key>selector|templateUrl|template|styleUrl)\s*:\s*(?P<q>['"`])(?P<val>.*?)(?P=q)""",
    re.DOTALL,
)
_BOOL_PROP_RE = re.compile(r"""(?P<key>standalone)\s*:\s*(?P<val>true|false)\b""")
_ARRAY_PROP_RE = re.compile(
    r"""(?P<key>styleUrls|styles|imports|providers|inputs|outputs)\s*:\s*\[(?P<body>.*?)]""",
    re.DOTALL,
)
_ARRAY_STRING_RE = re.compile(r"""(['"`])(?P<val>.*?)\1""")
_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")

# Template selector usage: element tags and attribute selectors.
_TEMPLATE_TAG_RE = re.compile(r"</?\s*([a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*)\b", re.I)
_TEMPLATE_ATTR_RE = re.compile(r"\[([a-zA-Z_][\w.-]*)]|\b([a-z][a-z0-9-]*)\s*=")


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _line_at(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _has_angular_signals(path: str, source: str) -> bool:
    if _ANGULAR_IMPORT_RE.search(source):
        return True
    if _DECORATOR_RE.search(source):
        return True
    return False


def _extract_decorator_args(source: str, open_paren_index: int) -> tuple[str, int]:
    """Return ``(args_text, end_index)`` for balanced parentheses starting at ``(``."""
    if open_paren_index >= len(source) or source[open_paren_index] != "(":
        return "", open_paren_index
    depth = 0
    in_str: str | None = None
    escape = False
    i = open_paren_index
    while i < len(source):
        ch = source[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in {'"', "'", "`"}:
            in_str = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren_index + 1 : i], i + 1
        i += 1
    return source[open_paren_index + 1 :], len(source)


def _parse_string_array(body: str) -> list[str]:
    return [m.group("val") for m in _ARRAY_STRING_RE.finditer(body)]


def _parse_ident_array(body: str) -> list[str]:
    # Prefer identifiers (imports: [CommonModule, FooComponent]).
    strings = _parse_string_array(body)
    if strings:
        return strings
    return _IDENT_RE.findall(body)


def _parse_decorator_metadata(args: str) -> dict[str, object]:
    meta: dict[str, object] = {}
    for match in _STR_PROP_RE.finditer(args):
        meta[match.group("key")] = match.group("val")
    for match in _BOOL_PROP_RE.finditer(args):
        meta[match.group("key")] = match.group("val") == "true"
    for match in _ARRAY_PROP_RE.finditer(args):
        key = match.group("key")
        body = match.group("body")
        if key in {"styleUrls", "styles", "inputs", "outputs"}:
            if key == "styles":
                # Inline CSS strings.
                meta[key] = _parse_string_array(body)
            else:
                meta[key] = _parse_string_array(body) or _parse_ident_array(body)
        elif key in {"imports", "providers"}:
            meta[key] = _parse_ident_array(body)
    return meta


def _find_decorated_classes(source: str) -> list[dict[str, object]]:
    """Locate Angular-decorated classes with metadata and line ranges.

    Finds ``@Component`` / ``@Directive`` / ``@Pipe`` / ``@Injectable`` call
    sites (args may span many lines), then binds each to the following
    ``class`` declaration.
    """
    results_by_name: dict[str, dict[str, object]] = {}

    for dec in _DECORATOR_RE.finditer(source):
        dec_name = dec.group(1)
        paren = dec.end() - 1
        if paren < 0 or paren >= len(source) or source[paren] != "(":
            paren = source.find("(", dec.start())
        if paren < 0:
            continue
        args, args_end = _extract_decorator_args(source, paren)
        class_match = _CLASS_AFTER_RE.search(source, args_end)
        if class_match is None:
            continue
        # Reject if another class-like declaration keyword appears first? The
        # next ``class`` token is the decorated declaration in normal Angular.
        name = class_match.group("name")
        meta = _parse_decorator_metadata(args)
        dec_line = _line_at(source, dec.start())
        existing = results_by_name.get(name)
        if existing is None:
            results_by_name[name] = {
                "name": name,
                "role": _DECORATOR_ROLES[dec_name],
                "decorator": dec_name,
                "metadata": meta,
                "all_decorators": (dec_name,),
                "start_line": _line_at(source, class_match.start()),
                "decorator_line": dec_line,
            }
            continue
        # Stacked decorators: prefer Component, merge metadata.
        raw_decorators = existing["all_decorators"]
        all_decs = (
            tuple(str(value) for value in raw_decorators)
            if isinstance(raw_decorators, (list, tuple))
            else ()
        ) + (dec_name,)
        existing["all_decorators"] = all_decs
        if dec_name == "Component" or existing["decorator"] != "Component":
            if dec_name == "Component" or existing["decorator"] not in _DECORATOR_ROLES:
                existing["decorator"] = dec_name
                existing["role"] = _DECORATOR_ROLES[dec_name]
                existing["decorator_line"] = dec_line
                merged = dict(existing["metadata"])  # type: ignore[call-overload]
                merged.update(meta)
                existing["metadata"] = merged
            else:
                merged = dict(existing["metadata"])  # type: ignore[call-overload]
                merged.update(meta)
                existing["metadata"] = merged
        else:
            merged = dict(existing["metadata"])  # type: ignore[call-overload]
            merged.update(meta)
            existing["metadata"] = merged

    return list(results_by_name.values())


def _resolve_relative(base_path: str, target: str) -> str:
    if not target or target.startswith(("http:", "https:", "data:")):
        return target
    base_dir = PurePosixPath(base_path.replace("\\", "/")).parent
    # Normalize ./foo paths without resolving beyond repo root.
    joined = (base_dir / target).as_posix()
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


def _selectors_from_template(template: str) -> list[tuple[str, int]]:
    """Best-effort element/attribute selector names from an inline template."""
    found: list[tuple[str, int]] = []
    for match in _TEMPLATE_TAG_RE.finditer(template):
        tag = match.group(1)
        if tag.lower() in {
            "div",
            "span",
            "p",
            "a",
            "button",
            "input",
            "form",
            "img",
            "ul",
            "li",
            "table",
            "tr",
            "td",
            "th",
            "thead",
            "tbody",
            "ng-container",
            "ng-template",
            "ng-content",
        }:
            continue
        # Angular components often use element selectors like app-foo.
        if "-" in tag or tag[:1].isupper():
            found.append((tag, template[: match.start()].count("\n") + 1))
    return found


class AngularEnricher:
    """Post-pass enricher for Angular decorated TypeScript classes."""

    enricher_id = ENRICHER_ID
    enricher_version = ENRICHER_VERSION

    def applies(
        self,
        path: str,
        source: str,
        *,
        language: str | None = None,
        language_hint: str | None = None,
    ) -> bool:
        lang = (language or language_hint or "").strip().lower()
        ext = _path_extension(path)
        if lang and lang not in _ANGULAR_LANGS and ext not in _ANGULAR_EXTENSIONS:
            return False
        if ext and ext not in _ANGULAR_EXTENSIONS and lang not in _ANGULAR_LANGS:
            return False
        return _has_angular_signals(path, source)

    def enrich(  # noqa: PLR0912, PLR0915
        self,
        extraction: FileExtraction,
        source: str,
        *,
        path: str,
    ) -> FileExtraction:
        decorated = _find_decorated_classes(source)
        if not decorated:
            return extraction

        by_name = {d["name"]: d for d in decorated}
        units_out: list[ExtractedSemanticUnit] = []
        resource_links: list[ExtractedResourceLink] = list(extraction.resource_links)
        new_rels: list[ExtractedRelationship] = []
        selector_index: dict[str, str] = {}  # selector -> unit local_id

        for unit in extraction.semantic_units:
            if unit.language_kind != "class" or unit.unqualified_name not in by_name:
                units_out.append(unit)
                continue
            info = by_name[unit.unqualified_name]
            role = str(info["role"])
            raw_metadata = info["metadata"]
            dec_meta = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
            meta = dict(unit.metadata)
            meta["framework"] = "angular"
            meta["decorator"] = info["decorator"]
            meta["angular"] = dec_meta
            if "selector" in dec_meta:
                meta["selector"] = dec_meta["selector"]
                meta["resolution_keys"] = (dec_meta["selector"],)
                selector_index[str(dec_meta["selector"])] = unit.local_id
            updated = replace(unit, semantic_role=role, metadata=meta)
            units_out.append(updated)

            # Resource links + relationships for external template/styles.
            # Persistence requires start/end both set or both None.
            raw_dec_line = info["decorator_line"]
            dec_line = raw_dec_line if isinstance(raw_dec_line, int) else unit.start_line
            template_url = dec_meta.get("templateUrl")
            if isinstance(template_url, str) and template_url:
                target = _resolve_relative(path, template_url)
                resource_links.append(
                    ExtractedResourceLink(
                        source_unit_local_id=unit.local_id,
                        target_path=target,
                        resource_kind=RESOURCE_TEMPLATE,
                        start_line=dec_line,
                        end_line=dec_line,
                        metadata={"property": "templateUrl", "framework": "angular"},
                    )
                )
                new_rels.append(
                    ExtractedRelationship(
                        source_unit_local_id=unit.local_id,
                        target_path=target,
                        relation_kind=REL_TEMPLATE_OF,
                        start_line=dec_line,
                        end_line=dec_line,
                        confidence=1.0,
                        resolution_method="angular_decorator",
                        metadata={"property": "templateUrl"},
                    )
                )

            style_urls = dec_meta.get("styleUrls")
            if isinstance(style_urls, list):
                for style_url in style_urls:
                    if not isinstance(style_url, str) or not style_url:
                        continue
                    target = _resolve_relative(path, style_url)
                    resource_links.append(
                        ExtractedResourceLink(
                            source_unit_local_id=unit.local_id,
                            target_path=target,
                            resource_kind=RESOURCE_STYLE,
                            start_line=dec_line,
                            end_line=dec_line,
                            metadata={"property": "styleUrls", "framework": "angular"},
                        )
                    )
                    new_rels.append(
                        ExtractedRelationship(
                            source_unit_local_id=unit.local_id,
                            target_path=target,
                            relation_kind=REL_STYLE_OF,
                            start_line=dec_line,
                            end_line=dec_line,
                            confidence=1.0,
                            resolution_method="angular_decorator",
                            metadata={"property": "styleUrls"},
                        )
                    )

            style_url = dec_meta.get("styleUrl")
            if isinstance(style_url, str) and style_url:
                target = _resolve_relative(path, style_url)
                resource_links.append(
                    ExtractedResourceLink(
                        source_unit_local_id=unit.local_id,
                        target_path=target,
                        resource_kind=RESOURCE_STYLE,
                        start_line=dec_line,
                        end_line=dec_line,
                        metadata={"property": "styleUrl", "framework": "angular"},
                    )
                )
                new_rels.append(
                    ExtractedRelationship(
                        source_unit_local_id=unit.local_id,
                        target_path=target,
                        relation_kind=REL_STYLE_OF,
                        start_line=dec_line,
                        end_line=dec_line,
                        confidence=1.0,
                        resolution_method="angular_decorator",
                        metadata={"property": "styleUrl"},
                    )
                )

            imports = dec_meta.get("imports")
            if isinstance(imports, list):
                for imported in imports:
                    if not isinstance(imported, str):
                        continue
                    new_rels.append(
                        ExtractedRelationship(
                            source_unit_local_id=unit.local_id,
                            target_qualified_name=imported,
                            relation_kind=REL_IMPORTS,
                            start_line=dec_line,
                            end_line=dec_line,
                            confidence=0.75,
                            resolution_method="angular_decorator_imports",
                            metadata={"framework": "angular"},
                        )
                    )

            # Inline template selector usage → renders_component.
            template = dec_meta.get("template")
            if isinstance(template, str) and template.strip():
                for sel, rel_line in _selectors_from_template(template):
                    # Map relative template line onto decorator vicinity (best-effort).
                    new_rels.append(
                        ExtractedRelationship(
                            source_unit_local_id=unit.local_id,
                            target_qualified_name=sel,
                            target_local_id=selector_index.get(sel),
                            relation_kind=REL_RENDERS_COMPONENT,
                            start_line=dec_line,
                            end_line=dec_line,
                            confidence=0.55,
                            resolution_method="angular_template_selector",
                            metadata={
                                "selector": sel,
                                "framework": "angular",
                                "template_line": rel_line,
                            },
                        )
                    )

        relationships = tuple(
            sorted(
                (*extraction.relationships, *new_rels),
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
        resources_out = tuple(
            sorted(
                resource_links,
                key=lambda r: (
                    r.resource_kind,
                    r.source_unit_local_id,
                    r.target_path,
                    r.start_line or 0,
                ),
            )
        )
        return replace(
            extraction,
            semantic_units=tuple(units_out),
            relationships=relationships,
            resource_links=resources_out,
        )


def register_angular_enricher(registry: ExtractorRegistry) -> None:
    """Register the Angular enricher for TypeScript family languages."""
    registry.register_enricher(
        AngularEnricher(),
        languages=(LANG_TYPESCRIPT, LANG_TSX),
        extensions=tuple(_ANGULAR_EXTENSIONS),
        priority=15,
    )


__all__ = [
    "ENRICHER_ID",
    "ENRICHER_VERSION",
    "AngularEnricher",
    "register_angular_enricher",
]
