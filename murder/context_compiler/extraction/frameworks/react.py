"""React framework enricher over JS/TS/JSX/TSX base extractions.

Conservatively marks PascalCase top-level units as ``component`` when JSX /
heritage / export / JSX-usage evidence is present, marks ``use*`` functions as
``hook``, and derives ``renders_component`` edges from JSX tags.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from murder.context_compiler.extraction.common import find_enclosing_unit
from murder.context_compiler.extraction.models import (
    REL_RENDERS_COMPONENT,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import (
    LANG_JAVASCRIPT,
    LANG_JSX,
    LANG_TSX,
    LANG_TYPESCRIPT,
    ExtractorRegistry,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_JAVASCRIPT,
    GRAMMAR_TSX,
    GRAMMAR_TYPESCRIPT,
    parse_source,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

ENRICHER_ID = "react"
ENRICHER_VERSION = "react-1"

_REACT_LANGS = frozenset({LANG_JAVASCRIPT, LANG_JSX, LANG_TYPESCRIPT, LANG_TSX})
_REACT_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})

_PASCAL_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
_HOOK_RE = re.compile(r"^use[A-Z]")
_HTML_INTRINSICS = frozenset(
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
        "fragment",
        "Fragment",
    }
)

_REACT_IMPORT_RE = re.compile(
    r"""(?:from\s+['"]react(?:-dom(?:/client)?)?['"]|require\s*\(\s*['"]react)"""
)
_JSX_TAG_RE = re.compile(r"</?\s*([A-Z][A-Za-z0-9.]*)\b")


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _node_text(node: Node) -> str:
    raw = node.text
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _line_of(node: Node) -> int:
    return node.start_point[0] + 1


def _is_pascal_case(name: str) -> bool:
    return bool(_PASCAL_RE.match(name))


def _is_hook_name(name: str) -> bool:
    return bool(_HOOK_RE.match(name))


def _is_top_level(unit: ExtractedSemanticUnit) -> bool:
    return unit.parent_local_id is None


def _grammar_for_language(language: str) -> str:
    if language == LANG_TSX:
        return GRAMMAR_TSX
    if language in {LANG_TYPESCRIPT, "mts", "cts"}:
        return GRAMMAR_TYPESCRIPT
    return GRAMMAR_JAVASCRIPT


def _jsx_tag_name(node: Node) -> str | None:
    """Return the component tag name from a JSX opening/self-closing element."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        # Some grammars put the identifier as the first named child after `<`.
        for child in node.named_children:
            if child.type in {
                "identifier",
                "nested_identifier",
                "member_expression",
                "jsx_identifier",
                "jsx_namespace_name",
                "jsx_member_expression",
            }:
                name_node = child
                break
    if name_node is None:
        return None
    text = _node_text(name_node).strip()
    if not text:
        return None
    # Member forms like Foo.Bar — keep full text; first segment drives casing.
    return text


def _is_component_tag(tag: str) -> bool:
    if not tag or tag in _HTML_INTRINSICS:
        return False
    head = tag.split(".", 1)[0]
    if head in _HTML_INTRINSICS:
        return False
    # Lowercase HTML intrinsics / custom elements with hyphen stay non-components
    # unless PascalCase (React component convention).
    if "-" in head:
        return False
    return _is_pascal_case(head)


def _walk_jsx_tags(root: Node) -> list[tuple[str, int]]:
    """Collect ``(tag_name, line)`` for component-like JSX tags."""
    found: list[tuple[str, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        ntype = node.type
        if ntype in {"jsx_self_closing_element", "jsx_opening_element"}:
            tag = _jsx_tag_name(node)
            if tag and _is_component_tag(tag):
                found.append((tag, _line_of(node)))
        stack.extend(reversed(list(node.named_children)))
    return found


def _unit_contains_jsx(unit: ExtractedSemanticUnit, source_lines: Sequence[str]) -> bool:
    """Best-effort: unit span includes a JSX tag (``<`` + identifier)."""
    # Inclusive 1-based lines.
    start = max(unit.start_line - 1, 0)
    end = min(unit.end_line, len(source_lines))
    chunk = "\n".join(source_lines[start:end])
    if "<" not in chunk:
        return False
    # Match JSX-ish tags; exclude comparisons like ``a < b``.
    return bool(re.search(r"<\s*[A-Za-z/]", chunk))


def _extends_react_component(unit: ExtractedSemanticUnit) -> bool:
    if unit.language_kind != "class":
        return False
    sig = unit.signature or ""
    return bool(re.search(r"\bextends\s+(?:React\.)?(?:Pure)?Component\b", sig))


def _collect_jsx_usage_names(source: str, tree: Tree | None) -> set[str]:
    names: set[str] = set()
    if tree is not None:
        for tag, _line in _walk_jsx_tags(tree.root_node):
            names.add(tag.split(".", 1)[0])
        return names
    for match in _JSX_TAG_RE.finditer(source):
        names.add(match.group(1).split(".", 1)[0])
    return names


def _has_react_signals(path: str, source: str, language: str | None) -> bool:
    ext = _path_extension(path)
    if ext in {".jsx", ".tsx"}:
        return True
    if language in {LANG_JSX, LANG_TSX}:
        return True
    if _REACT_IMPORT_RE.search(source):
        return True
    if re.search(r"\bextends\s+(?:React\.)?(?:Pure)?Component\b", source):
        return True
    if _JSX_TAG_RE.search(source):
        return True
    if re.search(r"\bfunction\s+use[A-Z]", source) or re.search(
        r"\b(?:const|let|var)\s+use[A-Z]\s*=", source
    ):
        return True
    return False


class ReactEnricher:
    """Post-pass enricher that assigns React roles and render relationships."""

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
        lang = (language or language_hint or "").strip().lower() or None
        if lang and lang not in _REACT_LANGS and lang not in {"js", "ts"}:
            ext = _path_extension(path)
            if ext not in _REACT_EXTENSIONS:
                return False
        return _has_react_signals(path, source, lang)

    def enrich(
        self,
        extraction: FileExtraction,
        source: str,
        *,
        path: str,
    ) -> FileExtraction:
        language = extraction.language or LANG_JAVASCRIPT
        grammar = _grammar_for_language(language)
        tree = parse_source(grammar, source)
        if tree is None and language == LANG_TSX:
            tree = parse_source(GRAMMAR_TYPESCRIPT, source)
        if tree is None and language in {LANG_TYPESCRIPT, LANG_TSX}:
            tree = parse_source(GRAMMAR_JAVASCRIPT, source)

        source_lines = source.splitlines()
        jsx_used = _collect_jsx_usage_names(source, tree)

        # First pass: classify components / hooks.
        role_updates: dict[str, ExtractedSemanticUnit] = {}

        for unit in extraction.semantic_units:
            if unit.language_kind not in {"function", "class"}:
                continue
            name = unit.unqualified_name
            if _is_hook_name(name) and unit.language_kind == "function":
                meta = dict(unit.metadata)
                meta["framework"] = "react"
                role_updates[unit.local_id] = replace(unit, semantic_role="hook", metadata=meta)
                continue

            if not _is_pascal_case(name):
                continue
            if not _is_top_level(unit):
                # Nested PascalCase helpers are not treated as components unless
                # they have strong JSX evidence and are still unusual — skip.
                continue

            evidence: list[str] = []
            if _unit_contains_jsx(unit, source_lines):
                evidence.append("jsx")
            if _extends_react_component(unit):
                evidence.append("extends_component")
            if unit.exported:
                evidence.append("exported")
            if name in jsx_used:
                evidence.append("jsx_usage")

            # Conservative: need PascalCase + top-level + at least one strong
            # signal beyond PascalCase alone. Exported-only is accepted when
            # combined with PascalCase for common presentational files; prefer
            # JSX / heritage / usage when present.
            strong = {"jsx", "extends_component", "jsx_usage"}
            if not (evidence and (strong & set(evidence) or "exported" in evidence)):
                continue
            # Reject exported-only without any JSX/heritage/usage when the body
            # clearly has no JSX (already filtered) — exported alone is weak;
            # require exported AND (jsx|extends|usage) OR just jsx/extends/usage.
            if not (strong & set(evidence)):
                continue

            meta = dict(unit.metadata)
            meta["framework"] = "react"
            meta["component_evidence"] = tuple(evidence)
            updated = replace(unit, semantic_role="component", metadata=meta)
            role_updates[unit.local_id] = updated

        units = tuple(role_updates.get(u.local_id, u) for u in extraction.semantic_units)
        components = [u for u in units if u.semantic_role == "component"]

        # Second pass: renders_component from JSX tags.
        new_rels: list[ExtractedRelationship] = []
        seen: set[tuple[str | None, str, int]] = set()

        jsx_sites: list[tuple[str, int]]
        if tree is not None:
            jsx_sites = _walk_jsx_tags(tree.root_node)
        else:
            jsx_sites = [
                (m.group(1), source[: m.start()].count("\n") + 1)
                for m in _JSX_TAG_RE.finditer(source)
                if _is_component_tag(m.group(1))
            ]

        for tag, line in jsx_sites:
            target_name = tag.split(".", 1)[0]
            enclosing = find_enclosing_unit(components, line)
            if enclosing is None:
                # Fall back to any enclosing unit marked component from full set.
                enclosing = find_enclosing_unit(
                    [u for u in units if u.semantic_role == "component"], line
                )
            if enclosing is None:
                continue
            key = (enclosing.local_id, target_name, line)
            if key in seen:
                continue
            seen.add(key)
            # Prefer same-file local component target when present.
            local_target = next(
                (
                    u
                    for u in components
                    if u.unqualified_name == target_name and u.local_id != enclosing.local_id
                ),
                None,
            )
            new_rels.append(
                ExtractedRelationship(
                    source_unit_local_id=enclosing.local_id,
                    target_local_id=local_target.local_id if local_target else None,
                    target_qualified_name=target_name,
                    relation_kind=REL_RENDERS_COMPONENT,
                    start_line=line,
                    end_line=line,
                    confidence=0.85 if local_target else 0.7,
                    resolution_method="jsx_tag",
                    metadata={"tag": tag, "framework": "react"},
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
                    r.start_line or 0,
                ),
            )
        )
        return replace(
            extraction,
            semantic_units=units,
            relationships=relationships,
        )


def register_react_enricher(registry: ExtractorRegistry) -> None:
    """Register the React enricher for JS/TS family languages."""
    registry.register_enricher(
        ReactEnricher(),
        languages=(LANG_JAVASCRIPT, LANG_JSX, LANG_TYPESCRIPT, LANG_TSX),
        extensions=tuple(_REACT_EXTENSIONS),
        priority=20,
    )


__all__ = [
    "ENRICHER_ID",
    "ENRICHER_VERSION",
    "ReactEnricher",
    "register_react_enricher",
]
