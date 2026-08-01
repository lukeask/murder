"""Centralized tree-sitter grammar and parser loading.

Parsers and :class:`~tree_sitter.Language` objects are cached. Loading a grammar
is fail-soft: import or ABI failures are recorded and that grammar returns
``None`` without raising, so one missing grammar cannot disable all indexing.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from tree_sitter import Language, Node, Parser, Tree

from murder.context_compiler.extraction.common import (
    DiagnosticAccumulator,
    assign_disambiguators,
    assign_parents_by_enclosure,
    derive_contains_relationships,
    find_enclosing_unit,
)
from murder.context_compiler.extraction.models import (
    ExtractedImport,
    ExtractedReference,
    ExtractedRelationship,
    ExtractedResourceLink,
    ExtractedSemanticUnit,
    FileExtraction,
    ParseStatus,
)

# Stable grammar keys used by extractors and diagnostics.
GRAMMAR_JAVASCRIPT = "javascript"
GRAMMAR_TYPESCRIPT = "typescript"
GRAMMAR_TSX = "tsx"
GRAMMAR_RUST = "rust"
GRAMMAR_C = "c"
GRAMMAR_CPP = "cpp"
GRAMMAR_GO = "go"
GRAMMAR_HTML = "html"
GRAMMAR_CSS = "css"
GRAMMAR_SCSS = "scss"
GRAMMAR_LESS = "less"


@dataclass(frozen=True, slots=True)
class GrammarStatus:
    """Cached outcome of attempting to load one grammar."""

    key: str
    available: bool
    error: str | None = None
    package: str | None = None


_lock = threading.RLock()
_languages: dict[str, Language | None] = {}
_statuses: dict[str, GrammarStatus] = {}
_thread_local = threading.local()
_core_error: str | None = None
_core_checked = False


def _import_tree_sitter() -> tuple[type[Language], type[Parser]] | None:
    """Return ``(Language, Parser)`` or ``None`` if the core package is missing."""
    global _core_checked, _core_error  # noqa: PLW0603
    if _core_checked and _core_error is not None:
        return None
    try:
        from tree_sitter import Language as LanguageClass  # noqa: PLC0415
        from tree_sitter import Parser as ParserClass  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - environment dependent
        _core_checked = True
        _core_error = f"{type(exc).__name__}: {exc}"
        return None
    _core_checked = True
    _core_error = None
    return LanguageClass, ParserClass


def _load_javascript() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_javascript as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_typescript() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_typescript as mod  # noqa: PLC0415

    return LanguageClass(mod.language_typescript())


def _load_tsx() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_typescript as mod  # noqa: PLC0415

    return LanguageClass(mod.language_tsx())


def _load_rust() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_rust as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_c() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_c as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_cpp() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_cpp as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_go() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_go as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_html() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_html as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_css() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_css as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_scss() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_scss as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _load_less() -> Language:
    LanguageClass, _ParserClass = _require_core()
    import tree_sitter_less as mod  # noqa: PLC0415

    return LanguageClass(mod.language())


def _require_core() -> tuple[type[Language], type[Parser]]:
    core = _import_tree_sitter()
    if core is None:
        raise ImportError(_core_error or "tree-sitter is not available")
    return core


_LOADERS: dict[str, str] = {
    GRAMMAR_JAVASCRIPT: "tree-sitter-javascript",
    GRAMMAR_TYPESCRIPT: "tree-sitter-typescript",
    GRAMMAR_TSX: "tree-sitter-typescript",
    GRAMMAR_RUST: "tree-sitter-rust",
    GRAMMAR_C: "tree-sitter-c",
    GRAMMAR_CPP: "tree-sitter-cpp",
    GRAMMAR_GO: "tree-sitter-go",
    GRAMMAR_HTML: "tree-sitter-html",
    GRAMMAR_CSS: "tree-sitter-css",
    GRAMMAR_SCSS: "tree-sitter-scss",
    GRAMMAR_LESS: "tree-sitter-less",
}


def _run_loader(key: str) -> Language:
    # Dispatch by key (not a frozen function map) so tests can monkeypatch loaders.
    if key == GRAMMAR_JAVASCRIPT:
        return _load_javascript()
    if key == GRAMMAR_TYPESCRIPT:
        return _load_typescript()
    if key == GRAMMAR_TSX:
        return _load_tsx()
    if key == GRAMMAR_RUST:
        return _load_rust()
    if key == GRAMMAR_C:
        return _load_c()
    if key == GRAMMAR_CPP:
        return _load_cpp()
    if key == GRAMMAR_GO:
        return _load_go()
    if key == GRAMMAR_HTML:
        return _load_html()
    if key == GRAMMAR_CSS:
        return _load_css()
    if key == GRAMMAR_SCSS:
        return _load_scss()
    if key == GRAMMAR_LESS:
        return _load_less()
    raise KeyError(f"unknown grammar key: {key}")


def reset_grammar_cache() -> None:
    """Clear cached languages/parsers (tests)."""
    global _core_checked, _core_error  # noqa: PLW0603
    with _lock:
        _languages.clear()
        _statuses.clear()
        _core_checked = False
        _core_error = None
    parsers = getattr(_thread_local, "parsers", None)
    if parsers is not None:
        parsers.clear()


def grammar_status(key: str) -> GrammarStatus:
    """Return (and cache) availability for ``key``."""
    get_language(key)
    with _lock:
        status = _statuses.get(key)
        if status is not None:
            return status
        package = _LOADERS.get(key)
        return GrammarStatus(key=key, available=False, error="unknown grammar", package=package)


def grammar_available(key: str) -> bool:
    """Return whether ``key`` can be used for parsing."""
    return get_language(key) is not None


def get_language(key: str) -> Language | None:
    """Load and cache a grammar ``Language``, or ``None`` on failure."""
    with _lock:
        if key in _languages:
            return _languages[key]

    if key not in _LOADERS:
        status = GrammarStatus(key=key, available=False, error="unknown grammar key")
        with _lock:
            _statuses[key] = status
            _languages[key] = None
        return None

    package = _LOADERS[key]
    language: Language | None
    error: str | None
    try:
        if _import_tree_sitter() is None:
            raise ImportError(_core_error or "tree-sitter is not available")
        language = _run_loader(key)
        error = None
    except Exception as exc:
        language = None
        error = f"{type(exc).__name__}: {exc}"

    status = GrammarStatus(
        key=key,
        available=language is not None,
        error=error,
        package=package,
    )
    with _lock:
        _languages[key] = language
        _statuses[key] = status
    return language


def get_parser(key: str) -> Parser | None:
    """Return a thread-local :class:`~tree_sitter.Parser` for ``key``, or ``None``."""
    language = get_language(key)
    if language is None:
        return None
    core = _import_tree_sitter()
    if core is None:
        return None
    _LanguageClass, ParserClass = core

    raw_cache: Any = getattr(_thread_local, "parsers", None)
    if raw_cache is None:
        cache: dict[str, Parser] = {}
        _thread_local.parsers = cache
    else:
        cache = cast("dict[str, Parser]", raw_cache)

    parser = cache.get(key)
    if parser is None:
        parser = ParserClass(language)
        cache[key] = parser
    else:
        # Ensure language is current after cache resets / reloads.
        parser.language = language
    return parser


def parse_source(key: str, source: str) -> Tree | None:
    """Parse ``source`` with grammar ``key``. Returns ``None`` if unavailable."""
    parser = get_parser(key)
    if parser is None:
        return None
    # Pass bytes for stable point offsets across Python versions.
    return parser.parse(source.encode("utf-8"))


def core_import_error() -> str | None:
    """Return the tree-sitter core import error message, if any."""
    _import_tree_sitter()
    return _core_error


# ---------------------------------------------------------------------------
# Shared node helpers (used by language walkers)
# ---------------------------------------------------------------------------


def node_text(node: Node) -> str:
    """Decode node source text as UTF-8 (replacement on errors)."""
    raw = node.text
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def line_range(node: Node) -> tuple[int, int]:
    """Return 1-based inclusive ``(start_line, end_line)`` for ``node``."""
    return node.start_point[0] + 1, node.end_point[0] + 1


def child_by_field(node: Node, field: str) -> Node | None:
    """Return the named field child, or ``None``."""
    return node.child_by_field_name(field)


def named_children(node: Node) -> list[Node]:
    """Return named children of ``node``."""
    return list(node.named_children)


def remap_extraction_ids(
    *,
    id_map: dict[str, str],
    imports: Sequence[ExtractedImport],
    references: Sequence[ExtractedReference],
    relationships: Sequence[ExtractedRelationship],
    resource_links: Sequence[ExtractedResourceLink] = (),
) -> tuple[
    list[ExtractedImport],
    list[ExtractedReference],
    list[ExtractedRelationship],
    list[ExtractedResourceLink],
]:
    """Rewrite local_id references after disambiguation."""

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
    new_resources = [
        replace(
            item,
            source_unit_local_id=map_id(item.source_unit_local_id) or item.source_unit_local_id,
        )
        for item in resource_links
    ]
    return new_imports, new_refs, new_rels, new_resources


def attach_import_sources(
    units: Sequence[ExtractedSemanticUnit],
    imports: Sequence[ExtractedImport],
) -> list[ExtractedImport]:
    """Point imports without a source unit at the innermost enclosing unit."""
    if not units:
        return list(imports)
    updated: list[ExtractedImport] = []
    for item in imports:
        if item.source_unit_local_id is not None:
            updated.append(item)
            continue
        enclosing = find_enclosing_unit(units, item.start_line)
        if enclosing is None:
            updated.append(item)
        else:
            updated.append(replace(item, source_unit_local_id=enclosing.local_id))
    return updated


def finalize_extraction(
    *,
    path: str,
    language: str,
    backend: str,
    root: Node,
    units: Sequence[ExtractedSemanticUnit],
    imports: Sequence[ExtractedImport],
    references: Sequence[ExtractedReference],
    relationships: Sequence[ExtractedRelationship],
    diagnostics: DiagnosticAccumulator,
    resource_links: Sequence[ExtractedResourceLink] = (),
) -> FileExtraction:
    """Disambiguate, enclose, sort, and package a :class:`FileExtraction`."""
    provisional = tuple(replace(u, parent_local_id=None) for u in units)
    finalized = assign_disambiguators(provisional)
    id_map = {
        before.local_id: after.local_id
        for before, after in zip(provisional, finalized, strict=True)
    }
    finalized = assign_parents_by_enclosure(finalized)

    imports_l, refs_l, rels_l, resources_l = remap_extraction_ids(
        id_map=id_map,
        imports=imports,
        references=references,
        relationships=relationships,
        resource_links=resource_links,
    )
    imports_l = attach_import_sources(finalized, imports_l)

    contains = derive_contains_relationships(finalized)
    relationships_out = tuple(
        sorted(
            (*rels_l, *contains),
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
            imports_l,
            key=lambda i: (
                i.start_line,
                i.import_kind,
                i.module_specifier,
                i.imported_name or "",
            ),
        )
    )
    references_out = tuple(
        sorted(
            refs_l,
            key=lambda r: (r.start_line, r.reference_kind, r.identifier),
        )
    )
    resources_out = tuple(
        sorted(
            resources_l,
            key=lambda r: (
                r.start_line or 0,
                r.resource_kind,
                r.target_path,
            ),
        )
    )

    has_error = bool(root.has_error)
    if has_error:
        parse_status: ParseStatus = "partial"
        diagnostics.warning(
            "syntax errors present; extracted available structure"
            if finalized
            else "syntax errors present; little structure recovered",
            code="parse_error",
        )
    else:
        parse_status = "parsed"

    return FileExtraction(
        path=path,
        language=language,
        parse_status=parse_status,
        semantic_units=finalized,
        imports=imports_out,
        references=references_out,
        relationships=relationships_out,
        resource_links=resources_out,
        diagnostics=diagnostics.as_tuple(),
    )


def text_only_for_missing_grammar(
    *,
    path: str,
    language: str,
    backend: str,
    grammar: str,
    status: GrammarStatus,
) -> FileExtraction:
    """Return a text-only extraction when ``grammar`` cannot be loaded."""
    from murder.context_compiler.extraction.common import empty_file_extraction  # noqa: PLC0415

    diag = DiagnosticAccumulator(backend)
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


__all__ = [
    "GRAMMAR_C",
    "GRAMMAR_CPP",
    "GRAMMAR_CSS",
    "GRAMMAR_GO",
    "GRAMMAR_HTML",
    "GRAMMAR_JAVASCRIPT",
    "GRAMMAR_LESS",
    "GRAMMAR_RUST",
    "GRAMMAR_SCSS",
    "GRAMMAR_TSX",
    "GRAMMAR_TYPESCRIPT",
    "GrammarStatus",
    "attach_import_sources",
    "child_by_field",
    "core_import_error",
    "finalize_extraction",
    "get_language",
    "get_parser",
    "grammar_available",
    "grammar_status",
    "line_range",
    "named_children",
    "node_text",
    "parse_source",
    "remap_extraction_ids",
    "reset_grammar_cache",
    "text_only_for_missing_grammar",
]
