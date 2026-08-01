"""C / C++ tree-sitter extractors.

Indexes functions, classes, structs, enums, methods, namespaces, typedefs /
aliases, templates where addressable, includes, and inheritance. Calls/refs
are best-effort; no compiler-accurate overload resolution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    build_qualified_name,
    empty_file_extraction,
    is_exported_hint,
    make_local_id,
    normalize_signature,
)
from murder.context_compiler.extraction.models import (
    IMPORT_MODULE,
    REL_CALLS,
    REL_IMPORTS,
    REL_INHERITS,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import (
    EXTENSION_TO_LANGUAGE,
    LANG_C,
    LANG_CPP,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_C,
    GRAMMAR_CPP,
    child_by_field,
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

_C_EXTENSIONS = frozenset({".c", ".h"})
_CPP_EXTENSIONS = frozenset({".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx", ".h++", ".c++"})
# .h is ambiguous; prefer C unless language_hint says otherwise.


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _qualify(scope_stack: Sequence[str], name: str) -> str:
    if not scope_stack:
        return name
    return build_qualified_name(*scope_stack, name, separator="::")


def _identifier_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {
        "identifier",
        "type_identifier",
        "field_identifier",
        "namespace_identifier",
    }:
        text = node_text(node)
        return text or None
    if node.type == "qualified_identifier":
        text = node_text(node)
        return text.split("::")[-1] if text else None
    if node.type == "destructor_name":
        for child in named_children(node):
            name = _identifier_name(child)
            if name:
                return f"~{name}"
        return None
    return None


def _declarator_name(declarator: Node | None) -> str | None:
    """Walk through pointer/array/function declarators to the name."""
    current = declarator
    while current is not None:
        if current.type in {
            "identifier",
            "field_identifier",
            "type_identifier",
            "destructor_name",
            "qualified_identifier",
        }:
            return _identifier_name(current)
        if current.type in {
            "function_declarator",
            "pointer_declarator",
            "array_declarator",
            "parenthesized_declarator",
            "reference_declarator",
            "abstract_function_declarator",
        }:
            inner = child_by_field(current, "declarator")
            if inner is None and named_children(current):
                inner = named_children(current)[0]
            current = inner
            continue
        # Fallback: search named children for an identifier.
        for child in named_children(current):
            name = _declarator_name(child)
            if name:
                return name
        return None
    return None


def _include_path(node: Node) -> str | None:
    for child in named_children(node):
        if child.type == "string_literal":
            text = node_text(child)
            if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
                return text[1:-1]
            return text
        if child.type == "system_lib_string":
            text = node_text(child)
            if len(text) >= 2 and text[0] == "<" and text[-1] == ">":
                return text[1:-1]
            return text
    return None


@dataclass
class _State:
    language: str
    backend: str
    units: list[ExtractedSemanticUnit] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    diagnostics: DiagnosticAccumulator = field(default_factory=lambda: DiagnosticAccumulator(""))
    scope_stack: list[str] = field(default_factory=list)
    unit_stack: list[str] = field(default_factory=list)
    seen_calls: set[tuple[str | None, str, int]] = field(default_factory=set)
    in_class: bool = False

    def __post_init__(self) -> None:
        if not self.diagnostics.backend:
            self.diagnostics = DiagnosticAccumulator(self.backend)

    @property
    def parent_local_id(self) -> str | None:
        return self.unit_stack[-1] if self.unit_stack else None

    def add_unit(
        self,
        *,
        language_kind: str,
        name: str,
        node: Node,
        signature: str | None = None,
        exported: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> ExtractedSemanticUnit:
        qualified = _qualify(self.scope_stack, name)
        start, end = line_range(node)
        unit = ExtractedSemanticUnit(
            local_id=make_local_id(language_kind=language_kind, qualified_name=qualified),
            language_kind=language_kind,
            qualified_name=qualified,
            unqualified_name=name,
            start_line=start,
            end_line=end,
            signature=normalize_signature(signature),
            parent_local_id=self.parent_local_id,
            exported=is_exported_hint(
                language=self.language,
                unqualified_name=name,
                explicit_exported=exported,
            ),
            metadata=dict(metadata or {}),
        )
        self.units.append(unit)
        return unit


def _sig_function(node: Node, name: str) -> str:
    declarator = child_by_field(node, "declarator")
    type_node = child_by_field(node, "type")
    parts: list[str] = []
    if type_node is not None:
        parts.append(node_text(type_node))
    if declarator is not None:
        parts.append(node_text(declarator))
    else:
        parts.append(name)
    return " ".join(parts) if parts else name


def _record_call(state: _State, node: Node) -> None:
    fn = child_by_field(node, "function")
    if fn is None:
        return
    name: str | None
    if fn.type in {"identifier", "field_identifier"}:
        name = node_text(fn)
    elif fn.type == "field_expression":
        field = child_by_field(fn, "field")
        name = _identifier_name(field) or node_text(fn)
    elif fn.type == "qualified_identifier":
        name = node_text(fn).split("::")[-1]
    else:
        name = _identifier_name(fn)
    if not name:
        return
    start, end = line_range(node)
    key = (state.parent_local_id, name, start)
    if key in state.seen_calls:
        return
    state.seen_calls.add(key)
    state.references.append(
        ExtractedReference(
            source_unit_local_id=state.parent_local_id,
            identifier=name,
            reference_kind="call",
            start_line=start,
            end_line=end,
            candidate_qualified_names=(name,),
            resolution_method="local_name",
        )
    )
    state.relationships.append(
        ExtractedRelationship(
            source_unit_local_id=state.parent_local_id,
            target_qualified_name=name,
            relation_kind=REL_CALLS,
            start_line=start,
            end_line=end,
            confidence=0.55,
            resolution_method="call_expression",
        )
    )


def _visit_refs(state: _State, node: Node) -> None:
    stack = [node]
    while stack:
        current = stack.pop()
        ctype = current.type
        if ctype == "call_expression":
            _record_call(state, current)
            args = child_by_field(current, "arguments")
            if args is not None:
                stack.extend(reversed(named_children(args)))
            continue
        if ctype in {
            "function_definition",
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
            "namespace_definition",
            "type_definition",
            "alias_declaration",
            "template_declaration",
            "preproc_include",
            "declaration",
            "field_declaration",
        }:
            continue
        stack.extend(reversed(named_children(current)))


def _walk_base_clause(state: _State, class_unit: ExtractedSemanticUnit, clause: Node) -> None:
    for child in named_children(clause):
        if child.type in {"type_identifier", "qualified_identifier"}:
            name = node_text(child)
            if not name:
                continue
            start, end = line_range(child)
            state.references.append(
                ExtractedReference(
                    source_unit_local_id=class_unit.local_id,
                    identifier=name.split("::")[-1],
                    reference_kind="extends",
                    start_line=start,
                    end_line=end,
                    candidate_qualified_names=(name,),
                    resolution_method="local_name",
                )
            )
            state.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=class_unit.local_id,
                    target_qualified_name=name,
                    relation_kind=REL_INHERITS,
                    start_line=start,
                    end_line=end,
                    confidence=0.75,
                    resolution_method="base_class_clause",
                )
            )


def _visit_class_like(
    state: _State,
    node: Node,
    *,
    language_kind: str,
) -> ExtractedSemanticUnit | None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return None
    keyword = "class" if language_kind == "class" else language_kind
    unit = state.add_unit(
        language_kind=language_kind,
        name=name,
        node=node,
        signature=f"{keyword} {name}",
    )
    for child in named_children(node):
        if child.type == "base_class_clause":
            _walk_base_clause(state, unit, child)

    body = child_by_field(node, "body")
    if body is None:
        return unit
    prev_in_class = state.in_class
    state.in_class = True
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        for member in named_children(body):
            _visit_declaration(state, member)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()
        state.in_class = prev_in_class
    return unit


def _visit_function_definition(state: _State, node: Node) -> None:
    declarator = child_by_field(node, "declarator")
    name = _declarator_name(declarator)
    if not name:
        return
    kind = "method" if state.in_class else "function"
    # Out-of-line Foo::method — treat as method and scope under Foo when qualified.
    scoped_type: str | None = None
    if declarator is not None:
        inner = child_by_field(declarator, "declarator")
        if inner is not None and inner.type == "qualified_identifier":
            text = node_text(inner)
            parts = text.split("::")
            if len(parts) >= 2:
                scoped_type = parts[-2]
                kind = "method"
    unit = state.add_unit(
        language_kind=kind,
        name=name,
        node=node,
        signature=_sig_function(node, name),
        metadata={"receiver": scoped_type} if scoped_type else None,
    )
    body = child_by_field(node, "body")
    if body is None:
        return
    state.unit_stack.append(unit.local_id)
    try:
        _visit_refs(state, body)
    finally:
        state.unit_stack.pop()


def _visit_field_or_declaration(state: _State, node: Node) -> None:
    """Index method declarations inside classes; skip data fields."""
    declarator = child_by_field(node, "declarator")
    if declarator is None:
        # Could be multiple declarators as named children.
        for child in named_children(node):
            if child.type == "function_declarator":
                name = _declarator_name(child)
                if name and state.in_class:
                    state.add_unit(
                        language_kind="method",
                        name=name,
                        node=node,
                        signature=normalize_signature(
                            f"{node_text(child_by_field(node, 'type') or node)} {node_text(child)}"
                        ),
                    )
        return

    # Walk function declarators (possibly nested in pointers).
    current: Node | None = declarator
    is_function = False
    while current is not None:
        if current.type == "function_declarator":
            is_function = True
            break
        if current.type in {
            "pointer_declarator",
            "reference_declarator",
            "parenthesized_declarator",
        }:
            current = child_by_field(current, "declarator")
            continue
        break

    if not is_function:
        return
    name = _declarator_name(declarator)
    if not name:
        return
    kind = "method" if state.in_class else "function"
    state.add_unit(
        language_kind=kind,
        name=name,
        node=node,
        signature=normalize_signature(node_text(node).rstrip(";")),
    )


def _visit_namespace(state: _State, node: Node) -> None:
    name = _identifier_name(child_by_field(node, "name")) or "anonymous"
    unit = state.add_unit(
        language_kind="namespace",
        name=name,
        node=node,
        signature=f"namespace {name}",
    )
    body = child_by_field(node, "body")
    if body is None:
        return
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        for child in named_children(body):
            _visit_declaration(state, child)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _visit_template(state: _State, node: Node) -> None:
    """Unwrap template_declaration and index the underlying declaration."""
    params = child_by_field(node, "parameters")
    param_text = node_text(params) if params is not None else "<>"
    for child in named_children(node):
        if child.type == "template_parameter_list":
            continue
        before = len(state.units)
        _visit_declaration(state, child)
        # Tag newly added top-level units as templates.
        for unit in state.units[before:]:
            if unit.parent_local_id == state.parent_local_id:
                meta = dict(unit.metadata)
                meta["template"] = True
                meta["template_parameters"] = param_text
                # dataclasses are frozen — replace via list mutation with new unit
                idx = state.units.index(unit)
                from dataclasses import replace  # noqa: PLC0415

                state.units[idx] = replace(unit, metadata=meta)
        return


def _visit_declaration(state: _State, node: Node) -> None:  # noqa: PLR0911, PLR0912
    ntype = node.type
    if ntype == "preproc_include":
        path = _include_path(node)
        if path:
            start, end = line_range(node)
            state.imports.append(
                ExtractedImport(
                    source_unit_local_id=state.parent_local_id,
                    module_specifier=path,
                    import_kind=IMPORT_MODULE,
                    start_line=start,
                    end_line=end,
                    metadata={"include": True},
                )
            )
            state.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=state.parent_local_id,
                    target_path=path,
                    relation_kind=REL_IMPORTS,
                    start_line=start,
                    end_line=end,
                    confidence=1.0,
                    resolution_method="preproc_include",
                )
            )
        return
    if ntype == "function_definition":
        _visit_function_definition(state, node)
        return
    if ntype == "class_specifier":
        _visit_class_like(state, node, language_kind="class")
        return
    if ntype == "struct_specifier":
        # Anonymous struct used only inside typedef — still index if named.
        if child_by_field(node, "name") is not None:
            _visit_class_like(state, node, language_kind="struct")
        return
    if ntype == "enum_specifier":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="enum",
                name=name,
                node=node,
                signature=f"enum {name}",
            )
        return
    if ntype == "union_specifier":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="union",
                name=name,
                node=node,
                signature=f"union {name}",
            )
        return
    if ntype == "namespace_definition":
        _visit_namespace(state, node)
        return
    if ntype == "type_definition":
        # typedef … Name;
        declarator = None
        name = None
        for child in named_children(node):
            if child.type == "type_identifier" and child != child_by_field(node, "type"):
                name = node_text(child)
            if child.type in {"struct_specifier", "enum_specifier", "union_specifier"}:
                if child_by_field(child, "name") is not None:
                    _visit_declaration(state, child)
                elif child.type == "struct_specifier":
                    # typedef struct { … } Name;
                    pass
            if child.type in {
                "function_declarator",
                "pointer_declarator",
                "array_declarator",
                "type_identifier",
            }:
                declarator = child
        if name is None and declarator is not None:
            name = _declarator_name(declarator) or _identifier_name(declarator)
        # Prefer the last type_identifier as the typedef name.
        ids = [node_text(c) for c in named_children(node) if c.type == "type_identifier"]
        if ids:
            name = ids[-1]
        if name:
            state.add_unit(
                language_kind="typedef",
                name=name,
                node=node,
                signature=normalize_signature(f"typedef … {name}"),
            )
        return
    if ntype == "alias_declaration":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="type_alias",
                name=name,
                node=node,
                signature=normalize_signature(f"using {name} = …"),
            )
        return
    if ntype == "template_declaration":
        _visit_template(state, node)
        return
    if ntype in {"field_declaration", "declaration"}:
        # May contain nested class/struct or a function declarator.
        for child in named_children(node):
            if child.type in {
                "class_specifier",
                "struct_specifier",
                "enum_specifier",
                "union_specifier",
            }:
                _visit_declaration(state, child)
        _visit_field_or_declaration(state, node)
        return
    if ntype == "access_specifier":
        return
    if ntype == "linkage_specification":
        for child in named_children(node):
            if child.type == "declaration_list":
                for inner in named_children(child):
                    _visit_declaration(state, inner)
            else:
                _visit_declaration(state, child)
        return
    if ntype == "declaration_list":
        for child in named_children(node):
            _visit_declaration(state, child)
        return
    _visit_refs(state, node)


def extract_tree(*, path: str, language: str, backend: str, tree: Tree) -> FileExtraction:
    root = tree.root_node
    state = _State(language=language, backend=backend)
    for child in named_children(root):
        _visit_declaration(state, child)
    return finalize_extraction(
        path=path,
        language=language,
        backend=backend,
        root=root,
        units=state.units,
        imports=state.imports,
        references=state.references,
        relationships=state.relationships,
        diagnostics=state.diagnostics,
    )


def _resolve_language(path: str, language_hint: str | None) -> str:
    ext = _path_extension(path)
    if language_hint:
        hint = language_hint.strip().lower()
        if hint in {"c++", "cpp", "cxx", "cplusplus"}:
            return LANG_CPP
        if hint == "c":
            return LANG_C
    if ext in EXTENSION_TO_LANGUAGE:
        return EXTENSION_TO_LANGUAGE[ext]
    if ext in _CPP_EXTENSIONS:
        return LANG_CPP
    return LANG_C


def _grammar_for(language: str) -> str:
    return GRAMMAR_CPP if language == LANG_CPP else GRAMMAR_C


class CFamilyExtractor:
    """Structural extractor for C and C++."""

    extractor_id = "tree-sitter-c-family"
    extractor_version = "tree-sitter-c-family-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        ext = _path_extension(path)
        if ext in _C_EXTENSIONS or ext in _CPP_EXTENSIONS:
            return True
        if language_hint:
            hint = language_hint.strip().lower()
            return hint in {"c", "c++", "cpp", "cxx", "cplusplus"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        language = _resolve_language(path, language_hint)
        grammar = _grammar_for(language)
        status = grammar_status(grammar)
        # C++ may fall back to C grammar for a weaker parse.
        if not status.available and grammar == GRAMMAR_CPP:
            fallback = grammar_status(GRAMMAR_C)
            if fallback.available:
                grammar = GRAMMAR_C
                status = fallback
        if not status.available:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=grammar,
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


# Back-compat / explicit aliases if callers want C-only or C++-only ids later.
CExtractor = CFamilyExtractor
CppExtractor = CFamilyExtractor

__all__ = ["CExtractor", "CFamilyExtractor", "CppExtractor", "extract_tree"]
