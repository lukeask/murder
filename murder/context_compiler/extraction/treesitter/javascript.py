"""JavaScript / TypeScript (JSX / TSX) tree-sitter extractors.

Indexes retrieval-worthy declarations only: named functions, stable top-level
arrow assignments, classes/methods, interfaces, type aliases, enums,
namespaces/modules, and exported constants. Anonymous callbacks are not units.

Framework roles (React components, hooks, …) are left to enrichers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    assign_disambiguators,
    assign_parents_by_enclosure,
    build_qualified_name,
    derive_contains_relationships,
    empty_file_extraction,
    find_enclosing_unit,
    is_exported_hint,
    make_local_id,
    normalize_signature,
)
from murder.context_compiler.extraction.models import (
    IMPORT_DEFAULT,
    IMPORT_DYNAMIC,
    IMPORT_NAMED,
    IMPORT_NAMESPACE,
    IMPORT_SIDE_EFFECT,
    IMPORT_TYPE_ONLY,
    REL_CALLS,
    REL_EXPORTS,
    REL_IMPLEMENTS,
    REL_IMPORTS,
    REL_INHERITS,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
    ParseStatus,
)
from murder.context_compiler.extraction.registry import (
    EXTENSION_TO_LANGUAGE,
    LANG_JAVASCRIPT,
    LANG_JSX,
    LANG_TSX,
    LANG_TYPESCRIPT,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_JAVASCRIPT,
    GRAMMAR_TSX,
    GRAMMAR_TYPESCRIPT,
    grammar_status,
    parse_source,
)

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

_JS_EXTENSIONS = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_TS_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})

_FUNCTION_VALUE_TYPES = frozenset(
    {
        "arrow_function",
        "function_expression",
        "generator_function",
        "class",
        "class_expression",
    }
)

_CALL_CALLEE_SKIP = frozenset({"super", "import"})


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


def _line_range(node: Node) -> tuple[int, int]:
    # tree-sitter points are 0-based; extraction contracts use 1-based inclusive.
    return node.start_point[0] + 1, node.end_point[0] + 1


def _child_by_field(node: Node, field: str) -> Node | None:
    return node.child_by_field_name(field)


def _named_children(node: Node) -> list[Node]:
    return list(node.named_children)


def _is_exported_decl(node: Node) -> bool:
    """True when ``node`` is the declaration (or nested binding) of an export."""
    parent = node.parent
    while parent is not None:
        if parent.type == "export_statement":
            return True
        if parent.type in {
            "program",
            "statement_block",
            "class_body",
            "interface_body",
            "enum_body",
        }:
            return False
        parent = parent.parent
    return False


def _string_literal_value(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "string":
        text = _node_text(node)
        if len(text) >= 2 and text[0] in {'"', "'", "`"} and text[-1] == text[0]:  # noqa: PLR2004
            return text[1:-1]
        return text
    if node.type == "string_fragment":
        return _node_text(node)
    return None


def _identifier_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {
        "identifier",
        "type_identifier",
        "property_identifier",
        "private_property_identifier",
        "constant",
    }:
        return _node_text(node)
    if node.type == "string":
        return _string_literal_value(node)
    return None


def _qualify(scope_stack: Sequence[str], name: str) -> str:
    if not scope_stack:
        return name
    return build_qualified_name(*scope_stack, name)


@dataclass
class _ExtractionState:
    language: str
    backend: str
    units: list[ExtractedSemanticUnit] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    diagnostics: DiagnosticAccumulator = field(default_factory=lambda: DiagnosticAccumulator(""))
    scope_stack: list[str] = field(default_factory=list)
    unit_stack: list[str] = field(default_factory=list)
    seen_call_keys: set[tuple[str | None, str, int]] = field(default_factory=set)
    exported_names: set[str] = field(default_factory=set)

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
        start, end = _line_range(node)
        meta = dict(metadata or {})
        local_id = make_local_id(language_kind=language_kind, qualified_name=qualified)
        unit = ExtractedSemanticUnit(
            local_id=local_id,
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
            metadata=meta,
        )
        self.units.append(unit)
        if unit.exported:
            self.exported_names.add(unit.unqualified_name)
            self.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=unit.local_id,
                    target_qualified_name=unit.qualified_name,
                    relation_kind=REL_EXPORTS,
                    start_line=start,
                    end_line=end,
                    confidence=1.0,
                    resolution_method="export_keyword",
                )
            )
        return unit


def _signature_for_callable(node: Node, name: str) -> str:
    params = _child_by_field(node, "parameters")
    return_type = _child_by_field(node, "return_type")
    parts = [name]
    if params is not None:
        parts.append(_node_text(params))
    else:
        # Single-parameter arrow without parens: parameter field may be identifier.
        param = _child_by_field(node, "parameter")
        if param is not None:
            parts.append(f"({_node_text(param)})")
        else:
            parts.append("()")
    if return_type is not None:
        parts.append(_node_text(return_type))
    return " ".join(parts)


def _signature_for_class(node: Node, name: str) -> str:
    heritage = None
    for child in _named_children(node):
        if child.type == "class_heritage":
            heritage = child
            break
    if heritage is None:
        return f"class {name}"
    return normalize_signature(f"class {name} {_node_text(heritage)}") or f"class {name}"


def _walk_heritage(state: _ExtractionState, class_unit: ExtractedSemanticUnit, node: Node) -> None:
    for child in _named_children(node):
        if child.type == "class_heritage":
            for part in _named_children(child):
                if part.type == "extends_clause":
                    _record_type_refs(
                        state,
                        class_unit,
                        part,
                        relation_kind=REL_INHERITS,
                        reference_kind="extends",
                    )
                elif part.type == "implements_clause":
                    _record_type_refs(
                        state,
                        class_unit,
                        part,
                        relation_kind=REL_IMPLEMENTS,
                        reference_kind="implements",
                    )
                elif part.type in {"identifier", "type_identifier", "member_expression"}:
                    # JS grammar: bare identifier under class_heritage means extends.
                    _record_type_refs(
                        state,
                        class_unit,
                        part,
                        relation_kind=REL_INHERITS,
                        reference_kind="extends",
                    )


def _record_type_refs(
    state: _ExtractionState,
    source: ExtractedSemanticUnit,
    node: Node,
    *,
    relation_kind: str,
    reference_kind: str,
) -> None:
    names: list[tuple[str, Node]] = []

    def collect(n: Node) -> None:
        if n.type in {"identifier", "type_identifier"}:
            text = _node_text(n)
            if text:
                names.append((text, n))
            return
        if n.type == "member_expression":
            text = _node_text(n)
            if text:
                names.append((text, n))
            return
        if n.type == "expression_with_type_arguments":
            obj = _child_by_field(n, "expression") or (
                n.named_children[0] if n.named_children else None
            )
            if obj is not None:
                collect(obj)
            return
        for child in _named_children(n):
            if child.type in {
                "type_arguments",
                "type_parameters",
                "constraint",
                "default_type",
            }:
                continue
            collect(child)

    collect(node)
    seen: set[str] = set()
    for name, ref_node in names:
        if name in seen:
            continue
        seen.add(name)
        start, end = _line_range(ref_node)
        state.references.append(
            ExtractedReference(
                source_unit_local_id=source.local_id,
                identifier=name,
                reference_kind=reference_kind,
                start_line=start,
                end_line=end,
                candidate_qualified_names=(name,),
                resolution_method="local_name",
            )
        )
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=source.local_id,
                target_qualified_name=name,
                relation_kind=relation_kind,
                start_line=start,
                end_line=end,
                confidence=0.7,
                resolution_method="local_name",
            )
        )


def _extract_imports(state: _ExtractionState, node: Node) -> None:  # noqa: PLR0912
    start, end = _line_range(node)
    source_node = _child_by_field(node, "source")
    module = _string_literal_value(source_node) or ""
    is_type_import = any(c.type == "type" and not c.is_named for c in node.children)

    clause = None
    for child in _named_children(node):
        if child.type == "import_clause":
            clause = child
            break

    if clause is None:
        if module:
            state.imports.append(
                ExtractedImport(
                    source_unit_local_id=state.parent_local_id,
                    module_specifier=module,
                    import_kind=IMPORT_SIDE_EFFECT,
                    start_line=start,
                    end_line=end,
                )
            )
            state.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=state.parent_local_id,
                    target_path=module,
                    relation_kind=REL_IMPORTS,
                    start_line=start,
                    end_line=end,
                    confidence=1.0,
                    resolution_method="import_statement",
                )
            )
        return

    emitted = False
    for child in _named_children(clause):
        if child.type == "identifier":
            # default import
            name = _node_text(child)
            state.imports.append(
                ExtractedImport(
                    source_unit_local_id=state.parent_local_id,
                    module_specifier=module,
                    import_kind=IMPORT_TYPE_ONLY if is_type_import else IMPORT_DEFAULT,
                    imported_name="default",
                    local_alias=name,
                    start_line=start,
                    end_line=end,
                )
            )
            emitted = True
        elif child.type == "namespace_import":
            alias = None
            for part in _named_children(child):
                if part.type == "identifier":
                    alias = _node_text(part)
            state.imports.append(
                ExtractedImport(
                    source_unit_local_id=state.parent_local_id,
                    module_specifier=module,
                    import_kind=IMPORT_TYPE_ONLY if is_type_import else IMPORT_NAMESPACE,
                    imported_name="*",
                    local_alias=alias,
                    start_line=start,
                    end_line=end,
                )
            )
            emitted = True
        elif child.type == "named_imports":
            for spec in _named_children(child):
                if spec.type != "import_specifier":
                    continue
                spec_type_only = is_type_import or any(
                    c.type == "type" and not c.is_named for c in spec.children
                )
                name_node = _child_by_field(spec, "name")
                alias_node = _child_by_field(spec, "alias")
                imported = _identifier_name(name_node)
                alias = _identifier_name(alias_node)
                state.imports.append(
                    ExtractedImport(
                        source_unit_local_id=state.parent_local_id,
                        module_specifier=module,
                        import_kind=IMPORT_TYPE_ONLY if spec_type_only else IMPORT_NAMED,
                        imported_name=imported,
                        local_alias=alias,
                        start_line=start,
                        end_line=end,
                    )
                )
                emitted = True

    if emitted or module:
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=state.parent_local_id,
                target_path=module or None,
                relation_kind=REL_IMPORTS,
                start_line=start,
                end_line=end,
                confidence=1.0,
                resolution_method="import_statement",
            )
        )


def _extract_export_statement(state: _ExtractionState, node: Node) -> None:  # noqa: PLR0912
    # Re-exports / export clauses without a nested declaration we walk separately.
    declaration = _child_by_field(node, "declaration")
    value = _child_by_field(node, "value")
    source = _child_by_field(node, "source")
    start, end = _line_range(node)

    if declaration is not None:
        _visit_statement(state, declaration, exported=True)
        return

    # export default <expression>
    if value is not None:
        if value.type in {
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "abstract_class_declaration",
            "function_expression",
            "generator_function",
            "class",
            "arrow_function",
        }:
            name = _identifier_name(_child_by_field(value, "name")) or "default"
            kind = (
                "class"
                if value.type in {"class_declaration", "abstract_class_declaration", "class"}
                else "function"
            )
            unit = state.add_unit(
                language_kind=kind,
                name=name,
                node=value,
                signature=(
                    _signature_for_class(value, name)
                    if kind == "class"
                    else _signature_for_callable(value, name)
                ),
                exported=True,
                metadata={"default_export": True},
            )
            if kind == "class":
                _walk_heritage(state, unit, value)
                _visit_class_body(state, value, unit)
            elif value.type in {
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
                "generator_function",
                "arrow_function",
            }:
                state.unit_stack.append(unit.local_id)
                try:
                    body = _child_by_field(value, "body")
                    if body is not None:
                        _visit_body_for_refs(state, body)
                finally:
                    state.unit_stack.pop()
            return
        # export default identifier;
        if value.type == "identifier":
            name = _node_text(value)
            state.exported_names.add(name)
            state.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=state.parent_local_id,
                    target_qualified_name=_qualify(state.scope_stack, name),
                    relation_kind=REL_EXPORTS,
                    start_line=start,
                    end_line=end,
                    confidence=0.9,
                    resolution_method="export_default",
                    metadata={"exported_name": name},
                )
            )
            return

    module_specifier = _string_literal_value(source)
    # export { a, b as c } [from "..."]
    for child in _named_children(node):
        if child.type != "export_clause":
            continue
        for spec in _named_children(child):
            if spec.type != "export_specifier":
                continue
            export_name = _identifier_name(_child_by_field(spec, "name"))
            alias = _identifier_name(_child_by_field(spec, "alias"))
            if export_name:
                state.exported_names.add(alias or export_name)
                state.relationships.append(
                    ExtractedRelationship(
                        source_unit_local_id=state.parent_local_id,
                        target_qualified_name=alias or export_name,
                        target_path=module_specifier,
                        relation_kind=REL_EXPORTS,
                        start_line=start,
                        end_line=end,
                        confidence=1.0,
                        resolution_method="export_clause",
                        metadata={
                            "exported_name": export_name,
                            "local_alias": alias,
                        },
                    )
                )
            if module_specifier:
                state.imports.append(
                    ExtractedImport(
                        source_unit_local_id=state.parent_local_id,
                        module_specifier=module_specifier,
                        import_kind=IMPORT_NAMED,
                        imported_name=export_name,
                        local_alias=alias,
                        start_line=start,
                        end_line=end,
                        metadata={"re_export": True},
                    )
                )
        if module_specifier:
            state.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=state.parent_local_id,
                    target_path=module_specifier,
                    relation_kind=REL_IMPORTS,
                    start_line=start,
                    end_line=end,
                    confidence=1.0,
                    resolution_method="export_from",
                )
            )
        return

    # export * from "..."
    if module_specifier and any(c.type == "*" for c in node.children):
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=module_specifier,
                import_kind=IMPORT_NAMESPACE,
                imported_name="*",
                start_line=start,
                end_line=end,
                metadata={"re_export": True},
            )
        )
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=state.parent_local_id,
                target_path=module_specifier,
                relation_kind=REL_IMPORTS,
                start_line=start,
                end_line=end,
                confidence=1.0,
                resolution_method="export_star",
            )
        )
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=state.parent_local_id,
                target_path=module_specifier,
                relation_kind=REL_EXPORTS,
                start_line=start,
                end_line=end,
                confidence=1.0,
                resolution_method="export_star",
            )
        )


def _visit_class_body(
    state: _ExtractionState,
    class_node: Node,
    class_unit: ExtractedSemanticUnit,
) -> None:
    body = _child_by_field(class_node, "body")
    if body is None:
        return
    state.scope_stack.append(class_unit.unqualified_name)
    state.unit_stack.append(class_unit.local_id)
    try:
        for member in _named_children(body):
            if member.type == "method_definition":
                _visit_method(state, member)
            elif member.type == "abstract_method_signature":
                name = _identifier_name(_child_by_field(member, "name"))
                if not name:
                    continue
                state.add_unit(
                    language_kind="method",
                    name=name,
                    node=member,
                    signature=_signature_for_callable(member, name),
                    exported=class_unit.exported,
                    metadata={"abstract": True},
                )
            elif member.type in {"public_field_definition", "field_definition"}:
                # Index only function-valued fields (stable retrieval identity).
                name_node = _child_by_field(member, "name") or _child_by_field(member, "property")
                name = _identifier_name(name_node)
                value = _child_by_field(member, "value")
                if name and value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    kind = "method" if value.type != "class" else "class"
                    unit = state.add_unit(
                        language_kind=kind if kind == "class" else "method",
                        name=name,
                        node=member,
                        signature=(
                            _signature_for_class(value, name)
                            if value.type in {"class", "class_expression"}
                            else _signature_for_callable(value, name)
                        ),
                        exported=class_unit.exported,
                        metadata={"field": True},
                    )
                    if value.type in {
                        "arrow_function",
                        "function_expression",
                        "generator_function",
                    }:
                        state.unit_stack.append(unit.local_id)
                        try:
                            fn_body = _child_by_field(value, "body")
                            if fn_body is not None:
                                _visit_body_for_refs(state, fn_body)
                        finally:
                            state.unit_stack.pop()
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _visit_method(state: _ExtractionState, node: Node) -> None:
    name = _identifier_name(_child_by_field(node, "name"))
    if not name:
        return
    # Skip getters/setters? Still retrieval-worthy methods — keep them.
    is_static = any(c.type == "static" and not c.is_named for c in node.children)
    unit = state.add_unit(
        language_kind="method",
        name=name,
        node=node,
        signature=_signature_for_callable(node, name),
        exported=False,
        metadata={"static": is_static} if is_static else None,
    )
    state.unit_stack.append(unit.local_id)
    try:
        body = _child_by_field(node, "body")
        if body is not None:
            _visit_body_for_refs(state, body)
    finally:
        state.unit_stack.pop()


def _visit_function_like(
    state: _ExtractionState,
    node: Node,
    *,
    exported: bool,
    name_override: str | None = None,
) -> ExtractedSemanticUnit | None:
    name = name_override or _identifier_name(_child_by_field(node, "name"))
    if not name:
        return None
    unit = state.add_unit(
        language_kind="function",
        name=name,
        node=node,
        signature=_signature_for_callable(node, name),
        exported=exported or _is_exported_decl(node),
    )
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        body = _child_by_field(node, "body")
        if body is not None:
            # Nested named function declarations are addressable; anonymous
            # callbacks inside are not indexed as units (refs only).
            if body.type == "statement_block":
                for stmt in _named_children(body):
                    _visit_statement(state, stmt, exported=False)
            else:
                _visit_body_for_refs(state, body)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()
    return unit


def _visit_class_like(
    state: _ExtractionState,
    node: Node,
    *,
    exported: bool,
    name_override: str | None = None,
) -> ExtractedSemanticUnit | None:
    name = name_override or _identifier_name(_child_by_field(node, "name"))
    if not name:
        return None
    unit = state.add_unit(
        language_kind="class",
        name=name,
        node=node,
        signature=_signature_for_class(node, name),
        exported=exported or _is_exported_decl(node),
    )
    _walk_heritage(state, unit, node)
    _visit_class_body(state, node, unit)
    return unit


def _visit_lexical(
    state: _ExtractionState,
    node: Node,
    *,
    exported: bool,
) -> None:
    """Index stable named bindings that are functions/classes or exported consts."""
    is_export = exported or _is_exported_decl(node)
    for declarator in _named_children(node):
        if declarator.type != "variable_declarator":
            continue
        name_node = _child_by_field(declarator, "name")
        value = _child_by_field(declarator, "value")
        # Only simple identifier bindings (not destructuring).
        if name_node is None or name_node.type != "identifier":
            continue
        name = _node_text(name_node)
        if not name:
            continue

        if value is not None and value.type in {
            "arrow_function",
            "function_expression",
            "generator_function",
        }:
            # Arrow / function expression assigned to a stable name.
            unit = state.add_unit(
                language_kind="function",
                name=name,
                node=declarator,
                signature=_signature_for_callable(value, name),
                exported=is_export,
                metadata={"binding": node.type},
            )
            state.scope_stack.append(name)
            state.unit_stack.append(unit.local_id)
            try:
                body = _child_by_field(value, "body")
                if body is not None:
                    if body.type == "statement_block":
                        for stmt in _named_children(body):
                            _visit_statement(state, stmt, exported=False)
                    else:
                        _visit_body_for_refs(state, body)
            finally:
                state.unit_stack.pop()
                state.scope_stack.pop()
            continue

        if value is not None and value.type in {"class", "class_expression"}:
            unit = state.add_unit(
                language_kind="class",
                name=name,
                node=declarator,
                signature=_signature_for_class(value, name),
                exported=is_export,
                metadata={"binding": node.type},
            )
            _walk_heritage(state, unit, value)
            _visit_class_body(state, value, unit)
            continue

        if is_export:
            # Exported constant — structurally useful module binding.
            kind_token = _node_text(node).split(None, 1)[0] if _node_text(node) else "const"
            sig = normalize_signature(f"{kind_token} {name}") or name
            state.add_unit(
                language_kind="constant",
                name=name,
                node=declarator,
                signature=sig,
                exported=True,
                metadata={"binding": node.type},
            )


def _visit_namespace(
    state: _ExtractionState,
    node: Node,
    *,
    exported: bool,
    language_kind: str,
) -> None:
    name = _identifier_name(_child_by_field(node, "name"))
    if not name:
        # declare module "foo" — use string name.
        name = _string_literal_value(_child_by_field(node, "name"))
    if not name:
        return
    unit = state.add_unit(
        language_kind=language_kind,
        name=name,
        node=node,
        signature=f"{language_kind} {name}",
        exported=exported or _is_exported_decl(node),
    )
    body = _child_by_field(node, "body")
    if body is None:
        return
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        if body.type == "statement_block":
            for stmt in _named_children(body):
                _visit_statement(state, stmt, exported=False)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _callee_name(node: Node) -> str | None:
    if node.type in {"identifier", "property_identifier", "private_property_identifier"}:
        return _node_text(node)
    if node.type == "member_expression":
        prop = _child_by_field(node, "property")
        if prop is not None:
            return _node_text(prop)
        return _node_text(node)
    if node.type == "call_expression":
        # chained calls — use outer callee
        fn = _child_by_field(node, "function")
        return _callee_name(fn) if fn is not None else None
    return None


def _visit_body_for_refs(state: _ExtractionState, node: Node) -> None:
    """Best-effort call / identifier references; do not create units for callbacks."""
    stack = [node]
    while stack:
        current = stack.pop()
        ctype = current.type
        if ctype == "call_expression":
            fn = _child_by_field(current, "function")
            if fn is not None:
                name = _callee_name(fn)
                if name and name not in _CALL_CALLEE_SKIP:
                    start, end = _line_range(current)
                    key = (state.parent_local_id, name, start)
                    if key not in state.seen_call_keys:
                        state.seen_call_keys.add(key)
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
            # Continue into arguments (may contain nested calls) but not into
            # nested function bodies' declarations as units — refs only.
            args = _child_by_field(current, "arguments")
            if args is not None:
                stack.extend(reversed(_named_children(args)))
            continue
        if ctype == "import_expression" or (
            ctype == "call_expression"
            and _child_by_field(current, "function") is not None
            and _node_text(_child_by_field(current, "function")) == "import"  # type: ignore[arg-type]
        ):
            continue
        if ctype in {
            "arrow_function",
            "function_expression",
            "generator_function",
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "abstract_class_declaration",
            "class",
        }:
            # Nested anonymous/named forms inside expressions: scan bodies for
            # calls only; named nested function_declaration handled by statement walk.
            if ctype in {"function_declaration", "generator_function_declaration"}:
                continue
            body = _child_by_field(current, "body")
            if body is not None:
                stack.append(body)
            continue
        if ctype == "import_statement":
            continue
        stack.extend(reversed(_named_children(current)))


def _visit_dynamic_import(state: _ExtractionState, node: Node) -> None:
    # import("...")
    start, end = _line_range(node)
    module = None
    args = _child_by_field(node, "arguments")
    if args is not None and args.named_children:
        module = _string_literal_value(args.named_children[0])
    if not module:
        # import_expression form
        source = _child_by_field(node, "source")
        module = _string_literal_value(source)
    if module:
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=module,
                import_kind=IMPORT_DYNAMIC,
                start_line=start,
                end_line=end,
            )
        )


def _visit_statement(  # noqa: PLR0911, PLR0912
    state: _ExtractionState, node: Node, *, exported: bool
) -> None:
    ntype = node.type

    if ntype == "export_statement":
        _extract_export_statement(state, node)
        return
    if ntype == "import_statement":
        _extract_imports(state, node)
        return
    if ntype == "expression_statement":
        for child in _named_children(node):
            if child.type == "call_expression":
                fn = _child_by_field(child, "function")
                if fn is not None and fn.type == "import":
                    _visit_dynamic_import(state, child)
                else:
                    _visit_body_for_refs(state, child)
            elif child.type == "import_expression":
                _visit_dynamic_import(state, child)
            elif child.type == "lexical_declaration":
                _visit_lexical(state, child, exported=exported)
            elif child.type == "internal_module":
                _visit_namespace(state, child, exported=exported, language_kind="namespace")
        return
    if ntype in {"lexical_declaration", "variable_declaration"}:
        _visit_lexical(state, node, exported=exported)
        return
    if ntype in {"function_declaration", "generator_function_declaration"}:
        _visit_function_like(state, node, exported=exported)
        return
    if ntype in {"class_declaration", "abstract_class_declaration"}:
        _visit_class_like(state, node, exported=exported)
        return
    if ntype == "interface_declaration":
        name = _identifier_name(_child_by_field(node, "name"))
        if name:
            unit = state.add_unit(
                language_kind="interface",
                name=name,
                node=node,
                signature=f"interface {name}",
                exported=exported or _is_exported_decl(node),
            )
            for child in _named_children(node):
                if child.type == "extends_type_clause":
                    _record_type_refs(
                        state,
                        unit,
                        child,
                        relation_kind=REL_INHERITS,
                        reference_kind="extends",
                    )
        return
    if ntype == "type_alias_declaration":
        name = _identifier_name(_child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="type_alias",
                name=name,
                node=node,
                signature=normalize_signature(f"type {name} = …"),
                exported=exported or _is_exported_decl(node),
            )
        return
    if ntype == "enum_declaration":
        name = _identifier_name(_child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="enum",
                name=name,
                node=node,
                signature=f"enum {name}",
                exported=exported or _is_exported_decl(node),
            )
        return
    if ntype == "internal_module":
        _visit_namespace(state, node, exported=exported, language_kind="namespace")
        return
    if ntype == "module":
        _visit_namespace(state, node, exported=exported, language_kind="module")
        return
    if ntype == "ambient_declaration":
        for child in _named_children(node):
            _visit_statement(state, child, exported=exported)
        return
    if ntype == "statement_block":
        for child in _named_children(node):
            _visit_statement(state, child, exported=exported)
        return

    # Control-flow / return / other statements: references only, no new units.
    _visit_body_for_refs(state, node)


def _attach_import_sources(state: _ExtractionState) -> None:
    """Point module-level imports at the innermost enclosing unit when nested."""
    if not state.units:
        return
    updated: list[ExtractedImport] = []
    for item in state.imports:
        if item.source_unit_local_id is not None:
            updated.append(item)
            continue
        enclosing = find_enclosing_unit(state.units, item.start_line)
        if enclosing is None:
            updated.append(item)
        else:
            updated.append(replace(item, source_unit_local_id=enclosing.local_id))
    state.imports = updated


def _remap_extraction_ids(
    *,
    id_map: dict[str, str],
    imports: list[ExtractedImport],
    references: list[ExtractedReference],
    relationships: list[ExtractedRelationship],
) -> tuple[
    list[ExtractedImport],
    list[ExtractedReference],
    list[ExtractedRelationship],
]:
    def map_id(value: str | None) -> str | None:
        if value is None:
            return None
        return id_map.get(value, value)

    new_imports = [
        replace(item, source_unit_local_id=map_id(item.source_unit_local_id)) for item in imports
    ]
    new_refs = [
        replace(
            item,
            source_unit_local_id=map_id(item.source_unit_local_id),
            candidate_local_ids=tuple(map_id(x) or x for x in item.candidate_local_ids),
        )
        for item in references
    ]
    new_rels = [
        replace(
            item,
            source_unit_local_id=map_id(item.source_unit_local_id),
            target_local_id=map_id(item.target_local_id),
        )
        for item in relationships
    ]
    return new_imports, new_refs, new_rels


def extract_tree(
    *,
    path: str,
    language: str,
    backend: str,
    tree: Tree,
) -> FileExtraction:
    """Walk a parsed tree into a normalized :class:`FileExtraction`."""
    root = tree.root_node
    state = _ExtractionState(language=language, backend=backend)
    for child in _named_children(root):
        _visit_statement(state, child, exported=False)

    # Stabilize local_ids, then rebuild parents via enclosure so containment
    # edges never point at pre-disambiguation identities.
    provisional = tuple(replace(u, parent_local_id=None) for u in state.units)
    units = assign_disambiguators(provisional)
    id_map = {
        before.local_id: after.local_id for before, after in zip(provisional, units, strict=True)
    }
    units = assign_parents_by_enclosure(units)
    state.units = list(units)

    imports, references, relationships = _remap_extraction_ids(
        id_map=id_map,
        imports=state.imports,
        references=state.references,
        relationships=state.relationships,
    )
    state.imports = imports
    state.references = references
    state.relationships = relationships
    _attach_import_sources(state)

    contains = derive_contains_relationships(units)
    relationships_out = tuple(
        sorted(
            (*state.relationships, *contains),
            key=lambda r: (
                r.relation_kind,
                r.source_unit_local_id or "",
                r.target_local_id or "",
                r.target_qualified_name or "",
                r.start_line or 0,
            ),
        )
    )
    imports_out = tuple(
        sorted(
            state.imports,
            key=lambda i: (i.start_line, i.import_kind, i.module_specifier, i.imported_name or ""),
        )
    )
    references_out = tuple(
        sorted(
            state.references,
            key=lambda r: (r.start_line, r.reference_kind, r.identifier),
        )
    )

    has_error = bool(root.has_error)
    if has_error:
        parse_status: ParseStatus = "partial"
        state.diagnostics.warning(
            "syntax errors present; extracted available structure"
            if units
            else "syntax errors present; little structure recovered",
            code="parse_error",
        )
    else:
        parse_status = "parsed"

    return FileExtraction(
        path=path,
        language=language,
        parse_status=parse_status,
        semantic_units=units,
        imports=imports_out,
        references=references_out,
        relationships=relationships_out,
        diagnostics=state.diagnostics.as_tuple(),
    )


def _resolve_language(  # noqa: PLR0911
    path: str, language_hint: str | None, *, family: str
) -> str:
    ext = _path_extension(path)
    if ext and ext in EXTENSION_TO_LANGUAGE:
        return EXTENSION_TO_LANGUAGE[ext]
    if language_hint:
        hint = language_hint.strip().lower()
        if hint in {"ts", "typescript"}:
            return LANG_TYPESCRIPT
        if hint == "tsx":
            return LANG_TSX
        if hint in {"js", "javascript"}:
            return LANG_JAVASCRIPT
        if hint == "jsx":
            return LANG_JSX
        return hint
    return family


def _grammar_for(language: str) -> str:
    if language == LANG_TSX:
        return GRAMMAR_TSX
    if language in {LANG_TYPESCRIPT, "mts", "cts"}:
        return GRAMMAR_TYPESCRIPT
    # jsx and javascript share the JS grammar (JSX included).
    return GRAMMAR_JAVASCRIPT


class JavaScriptExtractor:
    """Structural extractor for JavaScript and JSX."""

    extractor_id = "tree-sitter-javascript"
    extractor_version = "tree-sitter-javascript-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        ext = _path_extension(path)
        if ext in _JS_EXTENSIONS:
            return True
        if language_hint:
            hint = language_hint.strip().lower()
            return hint in {"javascript", "js", "jsx"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        language = _resolve_language(path, language_hint, family=LANG_JAVASCRIPT)
        grammar = _grammar_for(language)
        status = grammar_status(grammar)
        if not status.available:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.warning(
                f"grammar {grammar!r} unavailable ({status.error}); text-only indexing",
                code="grammar_unavailable",
            )
            return empty_file_extraction(
                path,
                language,
                "text_only",
                diagnostics=diag.as_tuple(),
            )
        try:
            tree = parse_source(grammar, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(
                path,
                language,
                "failed",
                diagnostics=diag.as_tuple(),
            )
        if tree is None:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.warning(
                f"grammar {grammar!r} unavailable; text-only indexing",
                code="grammar_unavailable",
            )
            return empty_file_extraction(
                path,
                language,
                "text_only",
                diagnostics=diag.as_tuple(),
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


class TypeScriptExtractor:
    """Structural extractor for TypeScript and TSX."""

    extractor_id = "tree-sitter-typescript"
    extractor_version = "tree-sitter-typescript-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        ext = _path_extension(path)
        if ext in _TS_EXTENSIONS:
            return True
        if language_hint:
            hint = language_hint.strip().lower()
            return hint in {"typescript", "ts", "tsx"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        language = _resolve_language(path, language_hint, family=LANG_TYPESCRIPT)
        grammar = _grammar_for(language)
        # TSX needs the TSX grammar; fall back to typescript if tsx missing.
        status = grammar_status(grammar)
        if not status.available and grammar == GRAMMAR_TSX:
            fallback = grammar_status(GRAMMAR_TYPESCRIPT)
            if fallback.available:
                grammar = GRAMMAR_TYPESCRIPT
                status = fallback
        if not status.available:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.warning(
                f"grammar {grammar!r} unavailable ({status.error}); text-only indexing",
                code="grammar_unavailable",
            )
            return empty_file_extraction(
                path,
                language,
                "text_only",
                diagnostics=diag.as_tuple(),
            )
        try:
            tree = parse_source(grammar, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(
                path,
                language,
                "failed",
                diagnostics=diag.as_tuple(),
            )
        if tree is None:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.warning(
                f"grammar {grammar!r} unavailable; text-only indexing",
                code="grammar_unavailable",
            )
            return empty_file_extraction(
                path,
                language,
                "text_only",
                diagnostics=diag.as_tuple(),
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


__all__ = [
    "JavaScriptExtractor",
    "TypeScriptExtractor",
    "extract_tree",
]
