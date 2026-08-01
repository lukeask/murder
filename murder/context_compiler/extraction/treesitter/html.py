"""HTML tree-sitter extractor.

Indexes only retrieval-useful entities: custom elements, PascalCase framework
component tags, named templates/fragments, significant IDs and named slots,
and linked scripts/styles/resources. Ordinary tags stay lexical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    empty_file_extraction,
    is_exported_hint,
    make_local_id,
    normalize_signature,
)
from murder.context_compiler.extraction.models import (
    IMPORT_RESOURCE,
    REL_IMPORTS,
    RESOURCE_ASSET,
    RESOURCE_STYLE,
    RESOURCE_TEMPLATE,
    ExtractedImport,
    ExtractedRelationship,
    ExtractedResourceLink,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import LANG_HTML
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_HTML,
    finalize_extraction,
    grammar_status,
    line_range,
    named_children,
    node_text,
    parse_source,
    text_only_for_missing_grammar,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

_EXTENSIONS = frozenset({".html", ".htm"})

# Built-in / void-ish tags that are never custom components.
_BUILTIN_TAGS = frozenset(
    {
        "a",
        "abbr",
        "address",
        "area",
        "article",
        "aside",
        "audio",
        "b",
        "base",
        "bdi",
        "bdo",
        "blockquote",
        "body",
        "br",
        "button",
        "canvas",
        "caption",
        "cite",
        "code",
        "col",
        "colgroup",
        "data",
        "datalist",
        "dd",
        "del",
        "details",
        "dfn",
        "dialog",
        "div",
        "dl",
        "dt",
        "em",
        "embed",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hgroup",
        "hr",
        "html",
        "i",
        "iframe",
        "img",
        "input",
        "ins",
        "kbd",
        "label",
        "legend",
        "li",
        "link",
        "main",
        "map",
        "mark",
        "menu",
        "meta",
        "meter",
        "nav",
        "noscript",
        "object",
        "ol",
        "optgroup",
        "option",
        "output",
        "p",
        "param",
        "picture",
        "pre",
        "progress",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "script",
        "search",
        "section",
        "select",
        "slot",
        "small",
        "source",
        "span",
        "strong",
        "style",
        "sub",
        "summary",
        "sup",
        "svg",
        "table",
        "tbody",
        "td",
        "template",
        "textarea",
        "tfoot",
        "th",
        "thead",
        "time",
        "title",
        "tr",
        "track",
        "u",
        "ul",
        "var",
        "video",
        "wbr",
        "math",
        "path",
        "g",
        "use",
        "defs",
        "clippath",
        "circle",
        "rect",
        "line",
        "polyline",
        "polygon",
        "ellipse",
        "text",
        "tspan",
    }
)

_RESOURCE_ATTRS = frozenset({"href", "src", "data-src", "poster", "srcset"})


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _attrs(tag_node: Node) -> dict[str, str]:
    """Parse attributes from a start_tag / self_closing_tag node."""
    result: dict[str, str] = {}
    for child in named_children(tag_node):
        if child.type != "attribute":
            continue
        name = None
        value = None
        for part in named_children(child):
            if part.type == "attribute_name":
                name = node_text(part).lower()
            elif part.type == "attribute_value":
                value = node_text(part)
            elif part.type == "quoted_attribute_value":
                raw = node_text(part)
                if len(raw) >= 2 and raw[0] in {'"', "'"} and raw[-1] == raw[0]:
                    value = raw[1:-1]
                else:
                    value = raw
        if name is not None:
            result[name] = value if value is not None else ""
    return result


def _tag_name(element: Node) -> str | None:
    for child in named_children(element):
        if child.type in {"start_tag", "self_closing_tag"}:
            for part in named_children(child):
                if part.type == "tag_name":
                    return node_text(part)
        if child.type == "tag_name":
            return node_text(child)
    return None


def _start_tag(element: Node) -> Node | None:
    for child in named_children(element):
        if child.type in {"start_tag", "self_closing_tag"}:
            return child
    return None


def _is_custom_element(tag: str) -> bool:
    lower = tag.lower()
    if lower in _BUILTIN_TAGS:
        return False
    # Custom elements require a hyphen (HTML spec).
    return "-" in lower and not lower.startswith("-")


def _is_framework_component(tag: str) -> bool:
    """PascalCase / camelCase tags used by Vue/Angular-style templates."""
    if not tag or tag[0].islower():
        return False
    if tag.lower() in _BUILTIN_TAGS:
        return False
    # Must contain an uppercase letter beyond the first, or be multi-segment.
    return any(c.isupper() for c in tag[1:]) or tag[0].isupper()


def _resource_kind_for(tag: str, path: str) -> str:
    lower = tag.lower()
    if lower == "link" or path.endswith((".css", ".scss", ".sass", ".less")):
        return RESOURCE_STYLE
    if lower in {"script", "module"}:
        return RESOURCE_ASSET
    if lower == "template":
        return RESOURCE_TEMPLATE
    return RESOURCE_ASSET


@dataclass
class _State:
    language: str
    backend: str
    units: list[ExtractedSemanticUnit] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    resource_links: list[ExtractedResourceLink] = field(default_factory=list)
    diagnostics: DiagnosticAccumulator = field(default_factory=lambda: DiagnosticAccumulator(""))
    seen_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.diagnostics.backend:
            self.diagnostics = DiagnosticAccumulator(self.backend)

    def add_unit(
        self,
        *,
        language_kind: str,
        name: str,
        node: Node,
        signature: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ExtractedSemanticUnit:
        start, end = line_range(node)
        unit = ExtractedSemanticUnit(
            local_id=make_local_id(language_kind=language_kind, qualified_name=name),
            language_kind=language_kind,
            qualified_name=name,
            unqualified_name=name,
            start_line=start,
            end_line=end,
            signature=normalize_signature(signature),
            exported=is_exported_hint(
                language=self.language,
                unqualified_name=name,
                explicit_exported=True,
            ),
            metadata=dict(metadata or {}),
        )
        self.units.append(unit)
        return unit


def _record_resource(
    state: _State,
    *,
    tag: str,
    path: str,
    node: Node,
    source_unit_local_id: str | None = None,
) -> None:
    start, end = line_range(node)
    kind = _resource_kind_for(tag, path)
    state.imports.append(
        ExtractedImport(
            source_unit_local_id=source_unit_local_id,
            module_specifier=path,
            import_kind=IMPORT_RESOURCE,
            start_line=start,
            end_line=end,
            metadata={"html_tag": tag.lower()},
        )
    )
    state.relationships.append(
        ExtractedRelationship(
            source_unit_local_id=source_unit_local_id,
            target_path=path,
            relation_kind=REL_IMPORTS,
            start_line=start,
            end_line=end,
            confidence=1.0,
            resolution_method="html_resource",
        )
    )
    # Resource links require a source unit — invent a file-level synthetic only
    # when we already have a unit; otherwise imports alone are enough.
    if source_unit_local_id:
        state.resource_links.append(
            ExtractedResourceLink(
                source_unit_local_id=source_unit_local_id,
                target_path=path,
                resource_kind=kind,
                start_line=start,
                end_line=end,
            )
        )


def _visit_element(state: _State, node: Node) -> None:  # noqa: PLR0912
    # script_element / style_element are separate node types in the HTML grammar.
    ntype = node.type
    if ntype in {"script_element", "style_element", "element"}:
        tag = _tag_name(node) or ("script" if ntype == "script_element" else "style")
        start = _start_tag(node)
        attrs = _attrs(start) if start is not None else {}
        source_id: str | None = None

        tag_lower = tag.lower()

        # Named <template id="…">
        if tag_lower == "template":
            tid = attrs.get("id")
            if tid:
                unit = state.add_unit(
                    language_kind="template",
                    name=tid,
                    node=node,
                    signature=f'<template id="{tid}">',
                    metadata={"html_tag": "template"},
                )
                source_id = unit.local_id

        # Named slots
        elif tag_lower == "slot":
            slot_name = attrs.get("name")
            if slot_name:
                state.add_unit(
                    language_kind="slot",
                    name=slot_name,
                    node=node,
                    signature=f'<slot name="{slot_name}">',
                    metadata={"html_tag": "slot"},
                )

        # Custom elements (hyphenated)
        elif _is_custom_element(tag):
            unit = state.add_unit(
                language_kind="custom_element",
                name=tag,
                node=node,
                signature=f"<{tag}>",
                metadata={"html_tag": tag_lower},
            )
            source_id = unit.local_id

        # Framework component tags (PascalCase)
        elif _is_framework_component(tag):
            unit = state.add_unit(
                language_kind="component",
                name=tag,
                node=node,
                signature=f"<{tag}>",
                metadata={"html_tag": tag, "framework_component": True},
            )
            source_id = unit.local_id

        # Significant IDs on otherwise ordinary elements (skip if already templated).
        eid = attrs.get("id")
        if eid and tag_lower not in {"template"} and eid not in state.seen_ids:
            # Only index "significant" ids: non-trivial length, not auto-generated noise.
            if len(eid) >= 2 and not eid.startswith("ember"):
                state.seen_ids.add(eid)
                # Avoid duplicating custom-element/component units that already
                # capture the node; still record id as separate retrieval key when
                # the element itself was not indexed.
                if (
                    source_id is None
                    and not _is_custom_element(tag)
                    and not _is_framework_component(tag)
                ):
                    state.add_unit(
                        language_kind="element_id",
                        name=eid,
                        node=node,
                        signature=f'<{tag_lower} id="{eid}">',
                        metadata={"html_tag": tag_lower, "id": eid},
                    )

        # Linked resources
        for attr in _RESOURCE_ATTRS:
            path = attrs.get(attr)
            if not path or path.startswith(("data:", "javascript:", "mailto:", "#")):
                continue
            # srcset may contain multiple URLs — take first token.
            if attr == "srcset":
                path = path.split(",")[0].strip().split()[0]
            if tag_lower in {
                "link",
                "script",
                "img",
                "source",
                "video",
                "audio",
                "iframe",
            } or attr in {
                "href",
                "src",
            }:
                # Prefer stylesheet links and scripts; skip plain anchors.
                if tag_lower == "a":
                    continue
                if tag_lower == "link" and attrs.get("rel") not in {
                    None,
                    "",
                    "stylesheet",
                    "modulepreload",
                    "preload",
                    "icon",
                    "manifest",
                }:
                    # Still record stylesheet-ish; skip alternate etc. lightly.
                    if attrs.get("rel") not in {"stylesheet", "modulepreload", "preload"}:
                        if "stylesheet" not in (attrs.get("rel") or ""):
                            continue
                _record_resource(
                    state,
                    tag=tag_lower,
                    path=path,
                    node=node,
                    source_unit_local_id=source_id,
                )

        # Recurse into children.
        for child in named_children(node):
            if child.type in {"element", "script_element", "style_element"}:
                _visit_element(state, child)
        return

    for child in named_children(node):
        if child.type in {"element", "script_element", "style_element"}:
            _visit_element(state, child)


def extract_tree(*, path: str, language: str, backend: str, tree: Tree) -> FileExtraction:
    root = tree.root_node
    state = _State(language=language, backend=backend)
    _visit_element(state, root)
    return finalize_extraction(
        path=path,
        language=language,
        backend=backend,
        root=root,
        units=state.units,
        imports=state.imports,
        references=(),
        relationships=state.relationships,
        diagnostics=state.diagnostics,
        resource_links=state.resource_links,
    )


class HtmlExtractor:
    """Selective structural extractor for HTML."""

    extractor_id = "tree-sitter-html"
    extractor_version = "tree-sitter-html-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        if _path_extension(path) in _EXTENSIONS:
            return True
        if language_hint:
            return language_hint.strip().lower() in {"html", "htm"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        del language_hint
        language = LANG_HTML
        status = grammar_status(GRAMMAR_HTML)
        if not status.available:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_HTML,
                status=status,
            )
        try:
            tree = parse_source(GRAMMAR_HTML, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(path, language, "failed", diagnostics=diag.as_tuple())
        if tree is None:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_HTML,
                status=status,
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


__all__ = ["HtmlExtractor", "extract_tree"]
