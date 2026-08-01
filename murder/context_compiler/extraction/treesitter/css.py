"""CSS / SCSS / LESS tree-sitter extractors.

Indexes only useful named entities: ``@keyframes``, global/central custom
properties, CSS Module exported class names, named layers, and imports /
resources. Ordinary selectors are not indexed. Indented ``.sass`` has no
maintained grammar — returns text_only with a diagnostic.
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
    ExtractedImport,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import LANG_CSS
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_CSS,
    GRAMMAR_LESS,
    GRAMMAR_SCSS,
    GrammarStatus,
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

_EXTENSIONS = frozenset({".css", ".scss", ".sass", ".less"})


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _is_css_module(path: str) -> bool:
    name = PurePosixPath(path.replace("\\", "/")).name.lower()
    return ".module." in name


def _string_value(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {"string_value", "string"}:
        text = node_text(node)
        if len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
            return text[1:-1]
        return text
    if node.type == "string_content":
        return node_text(node) or None
    if node.type == "call_expression":
        # url("…") / url('…') — only inspect arguments, not the function name.
        for child in named_children(node):
            if child.type != "arguments":
                continue
            for arg in named_children(child):
                val = _string_value(arg)
                if val:
                    return val
        return None
    for child in named_children(node):
        val = _string_value(child)
        if val:
            return val
    return None


@dataclass
class _State:
    language: str
    backend: str
    path: str
    units: list[ExtractedSemanticUnit] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    diagnostics: DiagnosticAccumulator = field(default_factory=lambda: DiagnosticAccumulator(""))
    css_module: bool = False
    in_root_or_global: bool = False
    nest_depth: int = 0
    seen_names: set[tuple[str, str]] = field(default_factory=set)

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
        exported: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> ExtractedSemanticUnit | None:
        key = (language_kind, name)
        if key in self.seen_names:
            return None
        self.seen_names.add(key)
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
                explicit_exported=exported,
            ),
            metadata=dict(metadata or {}),
        )
        self.units.append(unit)
        return unit


def _is_root_selector(selectors: Node | None) -> bool:
    if selectors is None:
        return False
    text = node_text(selectors).replace(" ", "")
    return ":root" in text or text in {":root", "html", "body"} or ":global" in text


def _class_names_from_selectors(selectors: Node) -> list[str]:
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "class_selector":
            for child in named_children(n):
                if child.type == "class_name":
                    # class_name may wrap identifier
                    text = node_text(child).lstrip(".")
                    if text:
                        names.append(text)
                    return
            text = node_text(n).lstrip(".")
            if text:
                names.append(text)
            return
        for child in named_children(n):
            walk(child)

    walk(selectors)
    return names


def _visit_import(state: _State, node: Node) -> None:
    start, end = line_range(node)
    path = None
    for child in named_children(node):
        path = _string_value(child)
        if path:
            break
    if not path:
        # @import url(...) plain
        text = node_text(node)
        if "url(" in text:
            inner = text.split("url(", 1)[1]
            path = inner.split(")", 1)[0].strip().strip("\"'")
    if not path:
        return
    state.imports.append(
        ExtractedImport(
            source_unit_local_id=None,
            module_specifier=path,
            import_kind=IMPORT_RESOURCE,
            start_line=start,
            end_line=end,
            metadata={"css_import": True},
        )
    )
    state.relationships.append(
        ExtractedRelationship(
            source_unit_local_id=None,
            target_path=path,
            relation_kind=REL_IMPORTS,
            start_line=start,
            end_line=end,
            confidence=1.0,
            resolution_method="css_import",
        )
    )


def _visit_keyframes(state: _State, node: Node) -> None:
    name_node = None
    for child in named_children(node):
        if child.type == "keyframes_name":
            name_node = child
            break
    name = node_text(name_node) if name_node is not None else None
    if not name:
        return
    state.add_unit(
        language_kind="keyframes",
        name=name,
        node=node,
        signature=f"@keyframes {name}",
    )


def _visit_at_rule(state: _State, node: Node) -> None:
    keyword = None
    for child in named_children(node):
        if child.type == "at_keyword":
            keyword = node_text(child).lstrip("@").lower()
            break
    if keyword is None:
        # first token
        text = node_text(node)
        if text.startswith("@"):
            keyword = text[1:].split(None, 1)[0].lower()
    if keyword == "layer":
        # @layer name; or @layer name { … }
        layer_name = None
        for child in named_children(node):
            if child.type in {"keyword_query", "identifier", "plain_value"}:
                layer_name = node_text(child).strip()
                break
        if not layer_name:
            # Try text after @layer
            parts = node_text(node).split(None, 2)
            if len(parts) >= 2:
                layer_name = parts[1].rstrip(";{").strip()
        if layer_name and layer_name not in {"{", "}"}:
            # May be comma-separated list
            for raw_part in layer_name.split(","):
                part = raw_part.strip()
                if part:
                    state.add_unit(
                        language_kind="layer",
                        name=part,
                        node=node,
                        signature=f"@layer {part}",
                    )
        # Descend into block for nested useful entities.
        for child in named_children(node):
            if child.type == "block":
                _visit_block(state, child)
        return
    if keyword == "import":
        _visit_import(state, node)
        return
    # Other at-rules: scan nested blocks only.
    for child in named_children(node):
        if child.type == "block":
            _visit_block(state, child)


def _visit_declaration(state: _State, node: Node) -> None:
    prop = None
    for child in named_children(node):
        if child.type == "property_name":
            prop = node_text(child)
            break
    if not prop:
        return
    # Custom properties when globally / centrally declared (:root, html, :global).
    if prop.startswith("--") and state.in_root_or_global:
        state.add_unit(
            language_kind="custom_property",
            name=prop,
            node=node,
            signature=normalize_signature(node_text(node).rstrip(";")),
            metadata={"custom_property": True},
        )


def _visit_rule_set(state: _State, node: Node) -> None:
    selectors = None
    block = None
    for child in named_children(node):
        if child.type == "selectors":
            selectors = child
        elif child.type == "block":
            block = child
    is_root = _is_root_selector(selectors)
    prev = state.in_root_or_global
    if is_root:
        state.in_root_or_global = True

    # CSS Modules: export top-level class selectors.
    if state.css_module and selectors is not None and state.nest_depth == 0:
        for cname in _class_names_from_selectors(selectors):
            state.add_unit(
                language_kind="css_module_class",
                name=cname,
                node=node,
                signature=f".{cname}",
                exported=True,
                metadata={"css_module": True},
            )

    # Framework :global(.foo) — rare but useful.
    if selectors is not None and ":global" in node_text(selectors):
        for cname in _class_names_from_selectors(selectors):
            state.add_unit(
                language_kind="global_class",
                name=cname,
                node=node,
                signature=f":global(.{cname})",
                metadata={"global": True},
            )

    if block is not None:
        _visit_block(state, block)
    state.in_root_or_global = prev


def _visit_block(state: _State, node: Node) -> None:
    state.nest_depth += 1
    try:
        for child in named_children(node):
            _visit_statement(state, child)
    finally:
        state.nest_depth -= 1


def _visit_statement(state: _State, node: Node) -> None:
    ntype = node.type
    if ntype == "import_statement":
        _visit_import(state, node)
        return
    if ntype == "keyframes_statement":
        _visit_keyframes(state, node)
        return
    if ntype == "at_rule":
        _visit_at_rule(state, node)
        return
    if ntype == "rule_set":
        _visit_rule_set(state, node)
        return
    if ntype == "declaration":
        _visit_declaration(state, node)
        return
    # Nested / media / supports wrappers
    if ntype in {"media_statement", "supports_statement", "charset_statement"}:
        for child in named_children(node):
            if child.type == "block":
                _visit_block(state, child)
            else:
                _visit_statement(state, child)
        return
    for child in named_children(node):
        _visit_statement(state, child)


def extract_tree(
    *,
    path: str,
    language: str,
    backend: str,
    tree: Tree,
) -> FileExtraction:
    root = tree.root_node
    state = _State(
        language=language,
        backend=backend,
        path=path,
        css_module=_is_css_module(path),
    )
    for child in named_children(root):
        _visit_statement(state, child)
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
    )


def _grammar_for_extension(ext: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(primary_grammar, fallbacks)`` for an extension."""
    if ext == ".scss":
        return GRAMMAR_SCSS, (GRAMMAR_CSS,)
    if ext == ".less":
        return GRAMMAR_LESS, (GRAMMAR_CSS,)
    if ext == ".sass":
        # Indented Sass has no maintained Python grammar package.
        return GRAMMAR_SCSS, ()
    return GRAMMAR_CSS, ()


def _pick_grammar(ext: str) -> tuple[str | None, GrammarStatus | None, str | None]:
    """Return ``(grammar_key, status, diagnostic_code)``."""
    if ext == ".sass":
        # Prefer text_only: indented syntax will not parse as SCSS reliably.
        status = grammar_status(GRAMMAR_SCSS)
        return None, status, "sass_indented_unsupported"
    primary, fallbacks = _grammar_for_extension(ext)
    status = grammar_status(primary)
    if status.available:
        return primary, status, None
    for fb in fallbacks:
        fb_status = grammar_status(fb)
        if fb_status.available:
            return fb, fb_status, None
    return primary, status, None


class CssExtractor:
    """Selective structural extractor for CSS and related stylesheets."""

    extractor_id = "tree-sitter-css"
    extractor_version = "tree-sitter-css-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        if _path_extension(path) in _EXTENSIONS:
            return True
        if language_hint:
            return language_hint.strip().lower() in {"css", "scss", "sass", "less"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        del language_hint
        language = LANG_CSS
        ext = _path_extension(path)
        grammar, status, special = _pick_grammar(ext)

        if special == "sass_indented_unsupported":
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.warning(
                "indented .sass has no maintained tree-sitter grammar; text-only indexing",
                code="sass_indented_unsupported",
            )
            return empty_file_extraction(path, language, "text_only", diagnostics=diag.as_tuple())

        assert status is not None
        if grammar is None or not status.available:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=grammar or GRAMMAR_CSS,
                status=status,
            )
        try:
            tree = parse_source(grammar, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(path, language, "failed", diagnostics=diag.as_tuple())
        if tree is None:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=grammar,
                status=status,
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


__all__ = ["CssExtractor", "extract_tree"]
