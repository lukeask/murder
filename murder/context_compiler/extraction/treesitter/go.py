"""Go tree-sitter extractor.

Indexes functions, methods (with receiver association), structs, interfaces,
type aliases/definitions, and imports. Calls/refs are best-effort.
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
    IMPORT_NAMED,
    REL_CALLS,
    REL_IMPORTS,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import LANG_GO
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_GO,
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

_EXTENSIONS = frozenset({".go"})


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _qualify(scope_stack: Sequence[str], name: str) -> str:
    if not scope_stack:
        return name
    return build_qualified_name(*scope_stack, name)


def _is_exported_go(name: str) -> bool:
    return bool(name) and name[0].isupper()


def _identifier_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {
        "identifier",
        "type_identifier",
        "field_identifier",
        "package_identifier",
    }:
        text = node_text(node)
        return text or None
    return None


def _string_literal(node: Node | None) -> str | None:
    if node is None:
        return None
    text = node_text(node)
    if node.type == "interpreted_string_literal" and len(text) >= 2:
        return text[1:-1]
    if node.type == "raw_string_literal" and len(text) >= 2:
        return text[1:-1]
    return text or None


def _receiver_type_name(receiver: Node | None) -> tuple[str | None, bool]:
    """Return ``(type_name, is_pointer)`` from a method receiver parameter list."""
    if receiver is None:
        return None, False
    for param in named_children(receiver):
        if param.type != "parameter_declaration":
            continue
        type_node = child_by_field(param, "type")
        if type_node is None:
            # Sometimes type is the last named child.
            kids = named_children(param)
            type_node = kids[-1] if kids else None
        if type_node is None:
            continue
        if type_node.type == "pointer_type":
            inner = named_children(type_node)
            if inner:
                return _identifier_name(inner[0]) or node_text(inner[0]), True
            return node_text(type_node).lstrip("*"), True
        name = _identifier_name(type_node) or node_text(type_node)
        return name, False
    return None, False


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
    package_name: str | None = None

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
        exported: bool | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ExtractedSemanticUnit:
        qualified = _qualify(self.scope_stack, name)
        start, end = line_range(node)
        exp = _is_exported_go(name) if exported is None else exported
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
                explicit_exported=exp,
            ),
            metadata=dict(metadata or {}),
        )
        self.units.append(unit)
        return unit


def _sig_func(node: Node, name: str) -> str:
    params = child_by_field(node, "parameters")
    result = child_by_field(node, "result")
    parts = [f"func {name}"]
    if params is not None:
        parts.append(node_text(params))
    else:
        parts.append("()")
    if result is not None:
        parts.append(node_text(result))
    return " ".join(parts)


def _sig_method(node: Node, name: str, receiver_type: str | None, pointer: bool) -> str:
    recv = f"(*{receiver_type})" if pointer and receiver_type else f"({receiver_type or '?'})"
    params = child_by_field(node, "parameters")
    result = child_by_field(node, "result")
    parts = [f"func {recv} {name}"]
    if params is not None:
        parts.append(node_text(params))
    else:
        parts.append("()")
    if result is not None:
        parts.append(node_text(result))
    return " ".join(parts)


def _extract_import_spec(state: _State, spec: Node) -> None:
    start, end = line_range(spec)
    path = _string_literal(child_by_field(spec, "path"))
    if not path:
        return
    alias_node = child_by_field(spec, "name")
    alias = _identifier_name(alias_node)
    # import . "pkg" / import _ "pkg"
    if alias in {".", "_"}:
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=path,
                import_kind=IMPORT_MODULE,
                local_alias=alias,
                start_line=start,
                end_line=end,
            )
        )
    elif alias:
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=path,
                import_kind=IMPORT_NAMED,
                imported_name=alias,
                local_alias=alias,
                start_line=start,
                end_line=end,
            )
        )
    else:
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=path,
                import_kind=IMPORT_MODULE,
                start_line=start,
                end_line=end,
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
            resolution_method="import_spec",
        )
    )


def _record_call(state: _State, node: Node) -> None:
    fn = child_by_field(node, "function")
    if fn is None:
        return
    name: str | None = None
    if fn.type in {"identifier", "field_identifier"}:
        name = node_text(fn)
    elif fn.type == "selector_expression":
        field = child_by_field(fn, "field")
        name = _identifier_name(field) or node_text(fn)
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
            confidence=0.6,
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
            "function_declaration",
            "method_declaration",
            "type_declaration",
            "import_declaration",
        }:
            continue
        stack.extend(reversed(named_children(current)))


def _visit_type_spec(state: _State, node: Node) -> None:
    """Handle type_spec or type_alias under type_declaration."""
    if node.type == "type_alias":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="type_alias",
                name=name,
                node=node,
                signature=normalize_signature(f"type {name} = …"),
            )
        return
    if node.type != "type_spec":
        return
    name = _identifier_name(child_by_field(node, "name"))
    type_node = child_by_field(node, "type")
    if not name or type_node is None:
        return
    if type_node.type == "struct_type":
        kind = "struct"
        sig = f"type {name} struct"
    elif type_node.type == "interface_type":
        kind = "interface"
        sig = f"type {name} interface"
        unit = state.add_unit(language_kind=kind, name=name, node=node, signature=sig)
        # Interface method elems as signature-only methods.
        state.scope_stack.append(name)
        state.unit_stack.append(unit.local_id)
        try:
            for elem in named_children(type_node):
                if elem.type != "method_elem":
                    continue
                mname = _identifier_name(child_by_field(elem, "name"))
                if not mname:
                    # field_identifier may be first named child
                    for child in named_children(elem):
                        if child.type == "field_identifier":
                            mname = node_text(child)
                            break
                if mname:
                    state.add_unit(
                        language_kind="method",
                        name=mname,
                        node=elem,
                        signature=normalize_signature(node_text(elem)),
                        metadata={"interface_method": True},
                    )
        finally:
            state.unit_stack.pop()
            state.scope_stack.pop()
        return
    else:
        kind = "type"
        sig = f"type {name} …"
    state.add_unit(language_kind=kind, name=name, node=node, signature=sig)


def _visit_function(state: _State, node: Node) -> None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return
    unit = state.add_unit(
        language_kind="function",
        name=name,
        node=node,
        signature=_sig_func(node, name),
    )
    body = child_by_field(node, "body")
    if body is None:
        return
    state.unit_stack.append(unit.local_id)
    try:
        _visit_refs(state, body)
    finally:
        state.unit_stack.pop()


def _visit_method(state: _State, node: Node) -> None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return
    receiver = child_by_field(node, "receiver")
    recv_type, is_ptr = _receiver_type_name(receiver)
    meta: dict[str, object] = {}
    if recv_type:
        meta["receiver"] = recv_type
        meta["receiver_pointer"] = is_ptr
    if recv_type:
        state.scope_stack.append(recv_type)
    try:
        unit = state.add_unit(
            language_kind="method",
            name=name,
            node=node,
            signature=_sig_method(node, name, recv_type, is_ptr),
            metadata=meta or None,
        )
    finally:
        if recv_type:
            state.scope_stack.pop()

    body = child_by_field(node, "body")
    if body is None:
        return
    state.unit_stack.append(unit.local_id)
    try:
        _visit_refs(state, body)
    finally:
        state.unit_stack.pop()


def _visit_item(state: _State, node: Node) -> None:
    ntype = node.type
    if ntype == "package_clause":
        name = _identifier_name(child_by_field(node, "name"))
        if name is None:
            for child in named_children(node):
                if child.type == "package_identifier":
                    name = node_text(child)
                    break
        state.package_name = name
        return
    if ntype == "import_declaration":
        for child in named_children(node):
            if child.type == "import_spec":
                _extract_import_spec(state, child)
            elif child.type == "import_spec_list":
                for spec in named_children(child):
                    if spec.type == "import_spec":
                        _extract_import_spec(state, spec)
        return
    if ntype == "function_declaration":
        _visit_function(state, node)
        return
    if ntype == "method_declaration":
        _visit_method(state, node)
        return
    if ntype == "type_declaration":
        for child in named_children(node):
            _visit_type_spec(state, child)
        return
    _visit_refs(state, node)


def extract_tree(*, path: str, language: str, backend: str, tree: Tree) -> FileExtraction:
    root = tree.root_node
    state = _State(language=language, backend=backend)
    for child in named_children(root):
        _visit_item(state, child)
    if state.package_name:
        state.diagnostics.info(
            f"package {state.package_name}",
            code="package_name",
        )
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


class GoExtractor:
    """Structural extractor for Go (``.go``)."""

    extractor_id = "tree-sitter-go"
    extractor_version = "tree-sitter-go-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        if _path_extension(path) in _EXTENSIONS:
            return True
        if language_hint:
            hint = language_hint.strip().lower()
            return hint in {"go", "golang"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        del language_hint
        language = LANG_GO
        status = grammar_status(GRAMMAR_GO)
        if not status.available:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_GO,
                status=status,
            )
        try:
            tree = parse_source(GRAMMAR_GO, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(path, language, "failed", diagnostics=diag.as_tuple())
        if tree is None:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_GO,
                status=status,
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


__all__ = ["GoExtractor", "extract_tree"]
