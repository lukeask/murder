"""Python structural extractor using the stdlib ``ast`` module.

Returns normalized immutable :class:`FileExtraction` records only — no SQLite,
snapshots, or LLMs. Nested locals and every assignment are intentionally
omitted; only retrieval-worthy declarations are indexed.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    assign_disambiguators,
    assign_parents_by_enclosure,
    build_qualified_name,
    derive_contains_relationships,
    empty_file_extraction,
    find_enclosing_unit,
    inclusive_range,
    is_exported_hint,
    make_local_id,
    normalize_signature,
)
from murder.context_compiler.extraction.models import (
    IMPORT_MODULE,
    IMPORT_NAMED,
    IMPORT_NAMESPACE,
    REL_CALLS,
    REL_INHERITS,
    REL_REFERENCES,
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedSemanticUnit,
    FileExtraction,
    ParseStatus,
)

EXTRACTOR_ID = "python-ast"
EXTRACTOR_VERSION = "python-ast-1"
_LANGUAGE = "python"
_BACKEND = EXTRACTOR_ID

# Nested function bodies smaller than this (statement count, excluding a leading
# docstring) are treated as trivial and skipped.
_MIN_NESTED_BODY_STMTS = 2

_FuncNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class PythonAstExtractor:
    """LanguageExtractor for ``.py`` / ``.pyi`` via stdlib ``ast``."""

    extractor_id: str = EXTRACTOR_ID
    extractor_version: str = EXTRACTOR_VERSION

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        name = PurePosixPath(path.replace("\\", "/")).name.lower()
        if name.endswith(".py") or name.endswith(".pyi"):
            return True
        if language_hint is None:
            return False
        hint = language_hint.strip().lower()
        return hint in {"python", "py"}

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        del language_hint  # Path / registry already selected Python.
        diagnostics = DiagnosticAccumulator(backend=_BACKEND)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            diagnostics.error(
                f"syntax error: {exc.msg}",
                start_line=exc.lineno,
                end_line=exc.lineno,
                code="syntax_error",
            )
            return empty_file_extraction(
                path,
                _LANGUAGE,
                "failed",
                diagnostics=diagnostics.as_tuple(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            diagnostics.error(f"parse failed: {exc}", code="parse_failed")
            return empty_file_extraction(
                path,
                _LANGUAGE,
                "failed",
                diagnostics=diagnostics.as_tuple(),
            )

        module_qual = module_qualifier_from_path(path)
        public_names = _parse_dunder_all(tree)

        collector = _Collector(
            module_qual=module_qual,
            public_names=public_names,
            diagnostics=diagnostics,
        )
        collector.collect_module(tree)

        units = assign_disambiguators(collector.units)
        # Parent links may reference pre-disambiguation local_ids; rebuild via
        # enclosure after local_ids stabilize, preserving explicit class→method
        # parents that still resolve.
        units = _refresh_parent_links(units, collector.parent_hints)
        units = assign_parents_by_enclosure(units)

        references = _resolve_local_reference_candidates(collector.references, units)

        relationships = list(derive_contains_relationships(units))
        relationships.extend(_resolve_local_inheritance(collector.relationships, units))
        relationships.extend(_local_call_and_ref_relationships(units, references))

        imports = tuple(collector.imports)
        relationships_sorted = tuple(
            sorted(
                relationships,
                key=lambda r: (
                    r.relation_kind,
                    r.source_unit_local_id or "",
                    r.target_local_id or "",
                    r.target_qualified_name or "",
                    r.start_line or 0,
                ),
            )
        )
        units_sorted = tuple(sorted(units, key=lambda u: (u.start_line, u.end_line, u.local_id)))
        references_sorted = tuple(
            sorted(
                references,
                key=lambda r: (r.start_line, r.end_line, r.identifier, r.reference_kind),
            )
        )

        parse_status: ParseStatus = "partial" if diagnostics.as_tuple() else "parsed"
        return FileExtraction(
            path=path,
            language=_LANGUAGE,
            parse_status=parse_status,
            semantic_units=units_sorted,
            imports=imports,
            references=references_sorted,
            relationships=relationships_sorted,
            diagnostics=diagnostics.as_tuple(),
        )


def module_qualifier_from_path(path: str) -> str:
    """Derive a dotted module qualifier from a relative file path.

    ``pkg/mod.py`` → ``pkg.mod``; ``pkg/__init__.py`` → ``pkg``; ``mod.py`` →
    ``mod``. Absolute-looking or empty stems fall back to the basename stem.
    """
    posix = PurePosixPath(path.replace("\\", "/"))
    parts = list(posix.parts)
    if parts and parts[0] == "/":
        parts = parts[1:]
    if not parts:
        return "module"
    name = parts[-1]
    stem = PurePosixPath(name).stem
    if stem == "__init__" and len(parts) >= 2:
        package_parts = parts[:-1]
    else:
        package_parts = [*parts[:-1], stem]
    cleaned = [p for p in package_parts if p and p != "."]
    if not cleaned:
        return stem or "module"
    return ".".join(cleaned)


@dataclass
class _Collector:
    module_qual: str
    public_names: frozenset[str] | None
    diagnostics: DiagnosticAccumulator
    units: list[ExtractedSemanticUnit] = field(default_factory=list)
    imports: list[ExtractedImport] = field(default_factory=list)
    references: list[ExtractedReference] = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    # Temporary (child_start_line, parent_local_id) hints before disambiguation.
    parent_hints: list[tuple[int, int, str]] = field(default_factory=list)

    def collect_module(self, tree: ast.Module) -> None:
        for node in tree.body:
            self._visit_stmt(node, qual_parts=(), parent_local_id=None, nested=False)
        self._extract_imports(tree)
        self._extract_calls_and_names(tree)

    def _visit_stmt(
        self,
        node: ast.stmt,
        *,
        qual_parts: tuple[str, ...],
        parent_local_id: str | None,
        nested: bool,
    ) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._visit_function(
                node,
                qual_parts=qual_parts,
                parent_local_id=parent_local_id,
                nested=nested,
                as_method=False,
            )
        elif isinstance(node, ast.ClassDef):
            self._visit_class(node, qual_parts=qual_parts, parent_local_id=parent_local_id)
        elif isinstance(node, ast.Assign) and not nested and not qual_parts:
            self._visit_assign_constant(node)
        elif isinstance(node, ast.AnnAssign) and not nested and not qual_parts:
            self._visit_annassign(node)
        else:
            type_alias = _maybe_type_alias_stmt(node)
            if type_alias is not None and not nested and not qual_parts:
                self._emit_type_alias(type_alias)

    def _visit_function(
        self,
        node: _FuncNode,
        *,
        qual_parts: tuple[str, ...],
        parent_local_id: str | None,
        nested: bool,
        as_method: bool,
    ) -> None:
        if nested and not _is_nontrivial_nested_function(node):
            return

        language_kind = "method" if as_method else "function"
        name_parts = (*qual_parts, node.name) if qual_parts else (node.name,)
        qualified = build_qualified_name(self.module_qual, *name_parts)
        start, end = _node_range(node)
        exported = self._exported_for(node.name, nested=nested or as_method)
        decorators = _decorator_names(node.decorator_list)
        metadata: dict[str, object] = {}
        if decorators:
            metadata["decorators"] = decorators
        if isinstance(node, ast.AsyncFunctionDef):
            metadata["async"] = True
        if nested:
            metadata["nested"] = True

        local_id = make_local_id(language_kind=language_kind, qualified_name=qualified)
        unit = ExtractedSemanticUnit(
            local_id=local_id,
            language_kind=language_kind,
            qualified_name=qualified,
            unqualified_name=node.name,
            start_line=start,
            end_line=end,
            signature=normalize_signature(_function_signature(node)),
            parent_local_id=parent_local_id,
            exported=exported,
            metadata=metadata,
        )
        self.units.append(unit)
        if parent_local_id is not None:
            self.parent_hints.append((start, end, parent_local_id))

        # Nested functions inside this function (not methods of a class).
        child_parts = name_parts
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function(
                    child,
                    qual_parts=child_parts,
                    parent_local_id=local_id,
                    nested=True,
                    as_method=False,
                )
            elif isinstance(child, ast.ClassDef):
                # Rare: class nested in function — still addressable.
                self._visit_class(
                    child,
                    qual_parts=child_parts,
                    parent_local_id=local_id,
                )

    def _visit_class(
        self,
        node: ast.ClassDef,
        *,
        qual_parts: tuple[str, ...],
        parent_local_id: str | None,
    ) -> None:
        name_parts = (*qual_parts, node.name) if qual_parts else (node.name,)
        qualified = build_qualified_name(self.module_qual, *name_parts)
        start, end = _node_range(node)
        decorators = _decorator_names(node.decorator_list)
        bases = [_unparse(base) for base in node.bases if _unparse(base)]
        metadata: dict[str, object] = {}
        if decorators:
            metadata["decorators"] = decorators
        if bases:
            metadata["bases"] = bases

        local_id = make_local_id(language_kind="class", qualified_name=qualified)
        unit = ExtractedSemanticUnit(
            local_id=local_id,
            language_kind="class",
            qualified_name=qualified,
            unqualified_name=node.name,
            start_line=start,
            end_line=end,
            signature=normalize_signature(_class_signature(node)),
            parent_local_id=parent_local_id,
            exported=self._exported_for(node.name, nested=bool(qual_parts)),
            metadata=metadata,
        )
        self.units.append(unit)
        if parent_local_id is not None:
            self.parent_hints.append((start, end, parent_local_id))

        for base_node, base_name in zip(node.bases, bases, strict=True):
            if not base_name:
                continue
            base_start = getattr(base_node, "lineno", start)
            self.relationships.append(
                ExtractedRelationship(
                    source_unit_local_id=local_id,
                    target_qualified_name=base_name,
                    relation_kind=REL_INHERITS,
                    start_line=base_start,
                    end_line=base_start,
                    confidence=0.7,
                    resolution_method="ast_base",
                    metadata={"base": base_name},
                )
            )
            self.references.append(
                ExtractedReference(
                    source_unit_local_id=local_id,
                    identifier=_base_identifier(base_name),
                    reference_kind="inheritance",
                    start_line=base_start,
                    end_line=base_start,
                    candidate_qualified_names=(base_name,),
                    resolution_method="ast_base",
                )
            )

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._visit_function(
                    child,
                    qual_parts=name_parts,
                    parent_local_id=local_id,
                    nested=False,
                    as_method=True,
                )
            elif isinstance(child, ast.ClassDef):
                self._visit_class(
                    child,
                    qual_parts=name_parts,
                    parent_local_id=local_id,
                )

    def _visit_assign_constant(self, node: ast.Assign) -> None:
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if not _is_constant_name(name):
                continue
            qualified = build_qualified_name(self.module_qual, name)
            start, end = _node_range(node)
            local_id = make_local_id(language_kind="constant", qualified_name=qualified)
            self.units.append(
                ExtractedSemanticUnit(
                    local_id=local_id,
                    language_kind="constant",
                    qualified_name=qualified,
                    unqualified_name=name,
                    start_line=start,
                    end_line=end,
                    signature=normalize_signature(name),
                    exported=self._exported_for(name, nested=False),
                    metadata={"constant_style": "screaming_snake"},
                )
            )

    def _visit_annassign(self, node: ast.AnnAssign) -> None:
        if not isinstance(node.target, ast.Name):
            return
        name = node.target.id
        annotation = _unparse(node.annotation)
        is_type_alias = _annotation_is_type_alias(node.annotation)
        if is_type_alias:
            self._emit_type_alias(
                _TypeAliasInfo(
                    name=name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", None) or node.lineno,
                    signature=_type_alias_signature(name, node.value),
                    metadata={"form": "annassign"},
                )
            )
            return
        if name.startswith("_"):
            return
        # Public annotated module-level bindings are structurally useful.
        qualified = build_qualified_name(self.module_qual, name)
        start, end = _node_range(node)
        sig = f"{name}: {annotation}" if annotation else name
        local_id = make_local_id(language_kind="constant", qualified_name=qualified)
        meta: dict[str, object] = {"constant_style": "annotated"}
        if annotation:
            meta["annotation"] = annotation
        self.units.append(
            ExtractedSemanticUnit(
                local_id=local_id,
                language_kind="constant",
                qualified_name=qualified,
                unqualified_name=name,
                start_line=start,
                end_line=end,
                signature=normalize_signature(sig),
                exported=self._exported_for(name, nested=False),
                metadata=meta,
            )
        )

    def _emit_type_alias(self, info: _TypeAliasInfo) -> None:
        qualified = build_qualified_name(self.module_qual, info.name)
        local_id = make_local_id(language_kind="type_alias", qualified_name=qualified)
        self.units.append(
            ExtractedSemanticUnit(
                local_id=local_id,
                language_kind="type_alias",
                qualified_name=qualified,
                unqualified_name=info.name,
                start_line=info.start_line,
                end_line=info.end_line,
                signature=normalize_signature(info.signature),
                exported=self._exported_for(info.name, nested=False),
                metadata=info.metadata,
            )
        )

    def _exported_for(self, name: str, *, nested: bool) -> bool:
        if nested:
            return False
        if self.public_names is not None:
            return name in self.public_names
        return is_exported_hint(language=_LANGUAGE, unqualified_name=name)

    def _extract_imports(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    local = alias.asname
                    kind = IMPORT_MODULE
                    self.imports.append(
                        ExtractedImport(
                            source_unit_local_id=None,
                            module_specifier=module,
                            import_kind=kind,
                            start_line=node.lineno,
                            end_line=getattr(node, "end_lineno", None) or node.lineno,
                            imported_name=None,
                            local_alias=local,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                level = node.level or 0
                specifier = ("." * level) + module
                if not node.names:
                    continue
                if len(node.names) == 1 and node.names[0].name == "*":
                    self.imports.append(
                        ExtractedImport(
                            source_unit_local_id=None,
                            module_specifier=specifier or ".",
                            import_kind=IMPORT_NAMESPACE,
                            start_line=node.lineno,
                            end_line=getattr(node, "end_lineno", None) or node.lineno,
                            imported_name="*",
                            local_alias=None,
                            metadata={"star": True},
                        )
                    )
                    continue
                for alias in node.names:
                    self.imports.append(
                        ExtractedImport(
                            source_unit_local_id=None,
                            module_specifier=specifier or ".",
                            import_kind=IMPORT_NAMED,
                            start_line=node.lineno,
                            end_line=getattr(node, "end_lineno", None) or node.lineno,
                            imported_name=alias.name,
                            local_alias=alias.asname,
                        )
                    )

    def _extract_calls_and_names(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                ident = _call_identifier(node.func)
                if not ident:
                    continue
                line = getattr(node, "lineno", None)
                if line is None:
                    continue
                end = getattr(node, "end_lineno", None) or line
                self.references.append(
                    ExtractedReference(
                        source_unit_local_id=None,  # filled later by enclosure
                        identifier=ident,
                        reference_kind="call",
                        start_line=line,
                        end_line=end,
                        resolution_method="ast_call",
                    )
                )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                # Skip names that are also recorded as call callees to avoid noise;
                # keep attribute-free loads that look like type/ref uses only when
                # they match a later unit name (resolved in post-pass).
                continue


@dataclass(frozen=True, slots=True)
class _TypeAliasInfo:
    name: str
    start_line: int
    end_line: int
    signature: str
    metadata: Mapping[str, object] = field(default_factory=dict)


def _maybe_type_alias_stmt(node: ast.stmt) -> _TypeAliasInfo | None:
    """Recognize ``ast.TypeAlias`` (3.12+) when the runtime provides it."""
    type_alias_cls = getattr(ast, "TypeAlias", None)
    if type_alias_cls is None or not isinstance(node, type_alias_cls):
        return None
    name_node = getattr(node, "name", None)
    if not isinstance(name_node, ast.Name):
        return None
    value = getattr(node, "value", None)
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", None) or start
    return _TypeAliasInfo(
        name=name_node.id,
        start_line=start,
        end_line=end,
        signature=_type_alias_signature(name_node.id, value),
        metadata={"form": "type_statement"},
    )


def _type_alias_signature(name: str, value: ast.AST | None) -> str:
    rendered = _unparse(value)
    if rendered:
        return f"{name} = {rendered}"
    return name


def _annotation_is_type_alias(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    text = _unparse(annotation)
    if not text:
        return False
    return text in {"TypeAlias", "typing.TypeAlias", "typing_extensions.TypeAlias"}


def _is_constant_name(name: str) -> bool:
    if not name or name.startswith("_"):
        return False
    return name.isupper() and any(c.isalpha() for c in name)


def _is_nontrivial_nested_function(node: _FuncNode) -> bool:
    """Named nested defs with enough body / decorators to be addressable."""
    if node.decorator_list:
        return True
    body = list(node.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    if not body:
        return False
    if len(body) == 1 and _is_trivial_stmt(body[0]):
        return False
    return len(body) >= _MIN_NESTED_BODY_STMTS or (len(body) == 1 and not _is_trivial_stmt(body[0]))


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_trivial_stmt(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return stmt.value.value is ...
    return False


def _parse_dunder_all(tree: ast.Module) -> frozenset[str] | None:
    """Return names listed in ``__all__`` when statically recognizable."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    names = _string_list_from_ast(node.value)
                    if names is not None:
                        return frozenset(names)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                if node.value is None:
                    continue
                names = _string_list_from_ast(node.value)
                if names is not None:
                    return frozenset(names)
    return None


def _string_list_from_ast(node: ast.AST) -> list[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
            else:
                return None
        return out
    return None


def _node_range(node: ast.AST) -> tuple[int, int]:
    start = getattr(node, "lineno", 1) or 1
    end = getattr(node, "end_lineno", None) or start
    # Include decorators in the start line when present.
    decorator_list = getattr(node, "decorator_list", None)
    if decorator_list:
        first = decorator_list[0]
        dec_line = getattr(first, "lineno", None)
        if dec_line is not None and dec_line < start:
            start = dec_line
    return inclusive_range(start, end)


def _decorator_names(decorators: Sequence[ast.expr]) -> tuple[str, ...]:
    names: list[str] = []
    for dec in decorators:
        text = _unparse(dec)
        if text:
            names.append(text)
    return tuple(names)


def _class_signature(node: ast.ClassDef) -> str:
    bases = [_unparse(base) for base in node.bases]
    bases += [f"{kw.arg}={_unparse(kw.value)}" for kw in node.keywords if kw.arg]
    if bases:
        return f"class {node.name}({', '.join(b for b in bases if b)})"
    return f"class {node.name}"


def _function_signature(node: _FuncNode) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    params = _render_params(node.args)
    sig = f"{prefix} {node.name}({params})"
    if node.returns is not None:
        ret = _unparse(node.returns)
        if ret:
            sig += f" -> {ret}"
    return sig


def _render_params(args: ast.arguments) -> str:
    parts: list[str] = []
    posonly = list(args.posonlyargs)
    regular = list(args.args)
    positional = posonly + regular
    defaults = list(args.defaults)
    default_offset = len(positional) - len(defaults)

    for idx, arg in enumerate(positional):
        has_default = idx >= default_offset
        parts.append(_render_arg(arg, has_default=has_default))
        if posonly and idx == len(posonly) - 1:
            parts.append("/")

    if args.vararg is not None:
        parts.append("*" + _render_arg(args.vararg, has_default=False))
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parts.append(_render_arg(arg, has_default=default is not None))

    if args.kwarg is not None:
        parts.append("**" + _render_arg(args.kwarg, has_default=False))

    return ", ".join(parts)


def _render_arg(arg: ast.arg, *, has_default: bool) -> str:
    rendered = arg.arg
    if arg.annotation is not None:
        annotation = _unparse(arg.annotation)
        if annotation:
            rendered += f": {annotation}"
    if has_default:
        rendered += "=..." if arg.annotation is None else " = ..."
    return rendered


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return ""


def _call_identifier(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _call_identifier(func.value)
        if base:
            return f"{base}.{func.attr}"
        return func.attr
    return None


def _base_identifier(base_name: str) -> str:
    # ``pkg.Base`` → keep full; for candidate matching also expose leaf.
    return base_name


def _refresh_parent_links(
    units: Sequence[ExtractedSemanticUnit],
    parent_hints: Sequence[tuple[int, int, str]],
) -> tuple[ExtractedSemanticUnit, ...]:
    """Re-attach parents after disambiguation changes local_ids.

    Hints store ``(start_line, end_line, old_parent_local_id)``. We match the
    unit by range and resolve the parent by matching the old parent's
    qualified identity through enclosure when the old id disappeared.
    """
    by_old_id = {u.local_id: u for u in units}
    # Map old parent ids that survived disambiguation unchanged.
    result: list[ExtractedSemanticUnit] = []
    hint_by_range = {(s, e): parent for s, e, parent in parent_hints}

    for unit in units:
        key = (unit.start_line, unit.end_line)
        old_parent = hint_by_range.get(key)
        if old_parent is None:
            # Keep existing parent if still valid.
            if unit.parent_local_id and unit.parent_local_id in by_old_id:
                result.append(unit)
            else:
                result.append(replace(unit, parent_local_id=None))
            continue
        if old_parent in by_old_id:
            result.append(replace(unit, parent_local_id=old_parent))
            continue
        # Parent was disambiguated — find by enclosure.
        parent = find_enclosing_unit(units, unit.start_line, exclude_local_ids=(unit.local_id,))
        result.append(replace(unit, parent_local_id=parent.local_id if parent else None))
    return tuple(result)


def _resolve_local_reference_candidates(
    references: Sequence[ExtractedReference],
    units: Sequence[ExtractedSemanticUnit],
) -> tuple[ExtractedReference, ...]:
    by_unqual: dict[str, list[ExtractedSemanticUnit]] = {}
    by_qual: dict[str, list[ExtractedSemanticUnit]] = {}
    for unit in units:
        by_unqual.setdefault(unit.unqualified_name, []).append(unit)
        by_qual.setdefault(unit.qualified_name, []).append(unit)

    resolved: list[ExtractedReference] = []
    for ref in references:
        source = find_enclosing_unit(units, ref.start_line)
        source_id = source.local_id if source else ref.source_unit_local_id

        ident = ref.identifier
        candidates = list(by_qual.get(ident, []))
        if not candidates:
            leaf = ident.rsplit(".", 1)[-1]
            candidates = list(by_unqual.get(leaf, []))
        # Drop definition-site self hits (decorator/name line coinciding with unit).
        candidates = [
            c
            for c in candidates
            if not (
                source_id
                and c.local_id == source_id
                and ref.reference_kind == "call"
                and c.start_line == ref.start_line
            )
        ]
        cand_ids = tuple(c.local_id for c in candidates)
        cand_quals = tuple(
            dict.fromkeys(
                [
                    *(ref.candidate_qualified_names or ()),
                    *(c.qualified_name for c in candidates),
                ]
            )
        )
        resolved.append(
            replace(
                ref,
                source_unit_local_id=source_id,
                candidate_local_ids=cand_ids,
                candidate_qualified_names=cand_quals,
                resolution_method=ref.resolution_method or ("local_name" if cand_ids else None),
            )
        )
    return tuple(resolved)


def _local_call_and_ref_relationships(
    units: Sequence[ExtractedSemanticUnit],
    references: Sequence[ExtractedReference],
) -> list[ExtractedRelationship]:
    by_id = {u.local_id: u for u in units}
    edges: list[ExtractedRelationship] = []
    seen: set[tuple[str | None, str, str]] = set()
    for ref in references:
        if ref.reference_kind not in {"call", "name"}:
            continue
        if not ref.candidate_local_ids:
            continue
        # Only emit edges for unambiguous local targets.
        if len(ref.candidate_local_ids) != 1:
            continue
        target_id = ref.candidate_local_ids[0]
        target = by_id.get(target_id)
        if target is None:
            continue
        relation = REL_CALLS if ref.reference_kind == "call" else REL_REFERENCES
        key = (ref.source_unit_local_id, target_id, relation)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            ExtractedRelationship(
                source_unit_local_id=ref.source_unit_local_id,
                target_local_id=target_id,
                target_qualified_name=target.qualified_name,
                relation_kind=relation,
                start_line=ref.start_line,
                end_line=ref.end_line,
                confidence=0.85,
                resolution_method="local_name",
            )
        )
    return edges


def _resolve_local_inheritance(
    relationships: Sequence[ExtractedRelationship],
    units: Sequence[ExtractedSemanticUnit],
) -> list[ExtractedRelationship]:
    """Fill ``target_local_id`` on inherits edges when the base is in-file."""
    by_unqual = {u.unqualified_name: u for u in units if u.language_kind == "class"}
    by_qual = {u.qualified_name: u for u in units if u.language_kind == "class"}
    out: list[ExtractedRelationship] = []
    for rel in relationships:
        if rel.relation_kind != REL_INHERITS or rel.target_local_id:
            out.append(rel)
            continue
        target_name = rel.target_qualified_name
        if not target_name:
            out.append(rel)
            continue
        target = by_qual.get(target_name) or by_unqual.get(target_name.rsplit(".", 1)[-1])
        if target is None:
            out.append(rel)
            continue
        out.append(
            replace(
                rel,
                target_local_id=target.local_id,
                target_qualified_name=target.qualified_name,
                confidence=0.95,
                resolution_method="local_name",
            )
        )
    return out


def register_python_extractor(registry: Any) -> None:
    """Register this extractor on an :class:`ExtractorRegistry`."""
    registry.register(
        PythonAstExtractor(),
        languages=(_LANGUAGE,),
        extensions=(".py", ".pyi"),
    )


__all__ = [
    "EXTRACTOR_ID",
    "EXTRACTOR_VERSION",
    "PythonAstExtractor",
    "module_qualifier_from_path",
    "register_python_extractor",
]
