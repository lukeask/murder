"""Rust tree-sitter extractor.

Indexes functions, structs, enums, traits, impl blocks, methods, type aliases,
modules, and use/imports. Trait impls and calls/refs are best-effort. Macro
invocations are not units; dense macro use may be noted diagnostically.
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
    REL_IMPLEMENTS,
    REL_IMPORTS,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
)
from murder.context_compiler.extraction.registry import LANG_RUST
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_RUST,
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

_EXTENSIONS = frozenset({".rs"})


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _qualify(scope_stack: Sequence[str], name: str) -> str:
    if not scope_stack:
        return name
    return build_qualified_name(*scope_stack, name)


def _has_pub(node: Node) -> bool:
    return any(c.type == "visibility_modifier" for c in named_children(node))


def _identifier_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type in {"identifier", "type_identifier", "field_identifier"}:
        text = node_text(node)
        return text or None
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
    macro_invocations: int = 0

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


def _sig_fn(node: Node, name: str) -> str:
    params = child_by_field(node, "parameters")
    ret = child_by_field(node, "return_type")
    parts = [f"fn {name}"]
    if params is not None:
        parts.append(node_text(params))
    else:
        parts.append("()")
    if ret is not None:
        parts.append(f"-> {node_text(ret)}")
    return " ".join(parts)


def _use_path_text(node: Node | None) -> str:
    if node is None:
        return ""
    return node_text(node).replace(" ", "")


def _collect_use_items(
    state: _State,
    argument: Node,
    *,
    start: int,
    end: int,
    prefix: str = "",
) -> None:
    """Emit import records for a use declaration argument tree."""
    ntype = argument.type
    if ntype == "identifier":
        name = node_text(argument)
        module = prefix.rstrip(":") if prefix else name
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=module or name,
                import_kind=IMPORT_NAMED if prefix else IMPORT_MODULE,
                imported_name=name if prefix else None,
                start_line=start,
                end_line=end,
            )
        )
        return
    if ntype == "scoped_identifier":
        path = _use_path_text(argument)
        # Last segment is the imported name; rest is module path.
        parts = path.split("::")
        if len(parts) >= 2:
            module = "::".join(parts[:-1])
            name = parts[-1]
            state.imports.append(
                ExtractedImport(
                    source_unit_local_id=state.parent_local_id,
                    module_specifier=module,
                    import_kind=IMPORT_NAMED,
                    imported_name=name,
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
        return
    if ntype == "use_as_clause":
        path_node = child_by_field(argument, "path")
        alias_node = child_by_field(argument, "alias")
        path = _use_path_text(path_node)
        alias = _identifier_name(alias_node)
        if prefix:
            module = prefix.rstrip(":")
            imported = path.split("::")[-1] if path else None
        elif "::" in path:
            parts = path.split("::")
            module = "::".join(parts[:-1])
            imported = parts[-1]
        else:
            module = path
            imported = path
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=module,
                import_kind=IMPORT_NAMED,
                imported_name=imported,
                local_alias=alias,
                start_line=start,
                end_line=end,
            )
        )
        return
    if ntype == "scoped_use_list":
        path_node = child_by_field(argument, "path")
        list_node = child_by_field(argument, "list")
        path = _use_path_text(path_node)
        new_prefix = f"{path}::" if path else prefix
        if list_node is not None:
            for child in named_children(list_node):
                _collect_use_items(state, child, start=start, end=end, prefix=new_prefix)
        return
    if ntype == "use_list":
        for child in named_children(argument):
            _collect_use_items(state, child, start=start, end=end, prefix=prefix)
        return
    if ntype == "use_wildcard":
        module = prefix.rstrip(":") if prefix else "*"
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=module,
                import_kind=IMPORT_MODULE,
                imported_name="*",
                start_line=start,
                end_line=end,
            )
        )
        return
    # Fallback: treat whole text as module path.
    text = _use_path_text(argument)
    if text:
        state.imports.append(
            ExtractedImport(
                source_unit_local_id=state.parent_local_id,
                module_specifier=text,
                import_kind=IMPORT_MODULE,
                start_line=start,
                end_line=end,
            )
        )


def _extract_use(state: _State, node: Node) -> None:
    start, end = line_range(node)
    argument = child_by_field(node, "argument")
    if argument is None:
        return
    before = len(state.imports)
    _collect_use_items(state, argument, start=start, end=end)
    if len(state.imports) > before:
        # One imports relationship per use declaration.
        module = state.imports[before].module_specifier
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=state.parent_local_id,
                target_path=module,
                relation_kind=REL_IMPORTS,
                start_line=start,
                end_line=end,
                confidence=1.0,
                resolution_method="use_declaration",
            )
        )


def _record_call(state: _State, node: Node) -> None:
    fn = child_by_field(node, "function")
    if fn is None:
        return
    name: str | None = None
    if fn.type in {"identifier", "field_identifier", "type_identifier"}:
        name = node_text(fn)
    elif fn.type == "scoped_identifier":
        name = node_text(fn).split("::")[-1]
    elif fn.type == "field_expression":
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
        if ctype == "macro_invocation":
            state.macro_invocations += 1
            continue
        if ctype in {
            "function_item",
            "function_signature_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "impl_item",
            "type_item",
            "mod_item",
            "use_declaration",
            "macro_definition",
        }:
            continue
        stack.extend(reversed(named_children(current)))


def _visit_function(state: _State, node: Node, *, as_method: bool = False) -> None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return
    kind = "method" if as_method else "function"
    unit = state.add_unit(
        language_kind=kind,
        name=name,
        node=node,
        signature=_sig_fn(node, name),
        exported=_has_pub(node),
    )
    body = child_by_field(node, "body")
    if body is None:
        return
    state.unit_stack.append(unit.local_id)
    try:
        for child in named_children(body):
            if child.type in {
                "function_item",
                "struct_item",
                "enum_item",
                "trait_item",
                "impl_item",
                "type_item",
                "mod_item",
                "use_declaration",
            }:
                _visit_item(state, child)
            else:
                _visit_refs(state, child)
    finally:
        state.unit_stack.pop()


def _visit_impl(state: _State, node: Node) -> None:
    trait_node = child_by_field(node, "trait")
    type_node = child_by_field(node, "type")
    type_name = _identifier_name(type_node) or node_text(type_node) if type_node else "impl"
    trait_name = _identifier_name(trait_node) if trait_node is not None else None

    if trait_name:
        impl_name = f"{trait_name} for {type_name}"
        kind = "impl"
        meta: dict[str, object] = {"trait": trait_name, "type": type_name}
    else:
        impl_name = type_name
        kind = "impl"
        meta = {"type": type_name}

    unit = state.add_unit(
        language_kind=kind,
        name=impl_name,
        node=node,
        signature=f"impl {impl_name}",
        exported=False,
        metadata=meta,
    )
    if trait_name:
        start, end = line_range(trait_node) if trait_node is not None else line_range(node)
        state.references.append(
            ExtractedReference(
                source_unit_local_id=unit.local_id,
                identifier=trait_name,
                reference_kind="implements",
                start_line=start,
                end_line=end,
                candidate_qualified_names=(trait_name,),
                resolution_method="local_name",
            )
        )
        state.relationships.append(
            ExtractedRelationship(
                source_unit_local_id=unit.local_id,
                target_qualified_name=trait_name,
                relation_kind=REL_IMPLEMENTS,
                start_line=start,
                end_line=end,
                confidence=0.8,
                resolution_method="impl_trait",
            )
        )

    body = child_by_field(node, "body")
    if body is None:
        return
    # Scope methods under the type name for stable qualified names.
    state.scope_stack.append(type_name)
    state.unit_stack.append(unit.local_id)
    try:
        for child in named_children(body):
            if child.type == "function_item":
                _visit_function(state, child, as_method=True)
            elif child.type == "function_signature_item":
                name = _identifier_name(child_by_field(child, "name"))
                if name:
                    state.add_unit(
                        language_kind="method",
                        name=name,
                        node=child,
                        signature=_sig_fn(child, name),
                        exported=_has_pub(child),
                        metadata={"signature_only": True},
                    )
            else:
                _visit_refs(state, child)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _visit_trait(state: _State, node: Node) -> None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return
    unit = state.add_unit(
        language_kind="trait",
        name=name,
        node=node,
        signature=f"trait {name}",
        exported=_has_pub(node),
    )
    body = child_by_field(node, "body")
    if body is None:
        return
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        for child in named_children(body):
            if child.type in {"function_item", "function_signature_item"}:
                mname = _identifier_name(child_by_field(child, "name"))
                if not mname:
                    continue
                method = state.add_unit(
                    language_kind="method",
                    name=mname,
                    node=child,
                    signature=_sig_fn(child, mname),
                    exported=_has_pub(child) or unit.exported,
                    metadata={"trait_method": True},
                )
                body_fn = child_by_field(child, "body")
                if body_fn is not None:
                    state.unit_stack.append(method.local_id)
                    try:
                        _visit_refs(state, body_fn)
                    finally:
                        state.unit_stack.pop()
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _visit_mod(state: _State, node: Node) -> None:
    name = _identifier_name(child_by_field(node, "name"))
    if not name:
        return
    unit = state.add_unit(
        language_kind="module",
        name=name,
        node=node,
        signature=f"mod {name}",
        exported=_has_pub(node),
    )
    body = child_by_field(node, "body")
    if body is None:
        # mod foo; — declaration only
        return
    state.scope_stack.append(name)
    state.unit_stack.append(unit.local_id)
    try:
        for child in named_children(body):
            _visit_item(state, child)
    finally:
        state.unit_stack.pop()
        state.scope_stack.pop()


def _visit_item(state: _State, node: Node) -> None:  # noqa: PLR0911, PLR0912
    ntype = node.type
    if ntype == "use_declaration":
        _extract_use(state, node)
        return
    if ntype == "function_item":
        _visit_function(state, node)
        return
    if ntype == "struct_item":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="struct",
                name=name,
                node=node,
                signature=f"struct {name}",
                exported=_has_pub(node),
            )
        return
    if ntype == "enum_item":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="enum",
                name=name,
                node=node,
                signature=f"enum {name}",
                exported=_has_pub(node),
            )
        return
    if ntype == "trait_item":
        _visit_trait(state, node)
        return
    if ntype == "impl_item":
        _visit_impl(state, node)
        return
    if ntype == "type_item":
        name = _identifier_name(child_by_field(node, "name"))
        if name:
            state.add_unit(
                language_kind="type_alias",
                name=name,
                node=node,
                signature=normalize_signature(f"type {name} = …"),
                exported=_has_pub(node),
            )
        return
    if ntype == "mod_item":
        _visit_mod(state, node)
        return
    if ntype == "macro_definition":
        # Named macro_rules! — skip as unit; optional diagnostic later.
        return
    if ntype == "const_item":
        # Skip ordinary constants unless pub and useful — keep lean.
        return
    if ntype == "static_item":
        return
    if ntype == "expression_statement":
        _visit_refs(state, node)
        return
    _visit_refs(state, node)


def extract_tree(*, path: str, language: str, backend: str, tree: Tree) -> FileExtraction:
    root = tree.root_node
    state = _State(language=language, backend=backend)
    for child in named_children(root):
        _visit_item(state, child)

    if state.macro_invocations >= 8:
        state.diagnostics.info(
            f"{state.macro_invocations} macro invocations; structure may be incomplete",
            code="macro_heavy",
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


class RustExtractor:
    """Structural extractor for Rust (``.rs``)."""

    extractor_id = "tree-sitter-rust"
    extractor_version = "tree-sitter-rust-1"

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        if _path_extension(path) in _EXTENSIONS:
            return True
        if language_hint:
            hint = language_hint.strip().lower()
            return hint in {"rust", "rs"}
        return False

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        del language_hint  # language fixed by extension / registry
        language = LANG_RUST
        status = grammar_status(GRAMMAR_RUST)
        if not status.available:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_RUST,
                status=status,
            )
        try:
            tree = parse_source(GRAMMAR_RUST, source)
        except Exception as exc:
            diag = DiagnosticAccumulator(self.extractor_id)
            diag.error(f"parse failed: {type(exc).__name__}: {exc}", code="parse_failed")
            return empty_file_extraction(path, language, "failed", diagnostics=diag.as_tuple())
        if tree is None:
            return text_only_for_missing_grammar(
                path=path,
                language=language,
                backend=self.extractor_id,
                grammar=GRAMMAR_RUST,
                status=status,
            )
        return extract_tree(
            path=path,
            language=language,
            backend=self.extractor_id,
            tree=tree,
        )


__all__ = ["RustExtractor", "extract_tree"]
