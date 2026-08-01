"""Tree-sitter backed language extractors for Context Assembler 2.

Grammars load fail-soft: a missing package disables only that language
family, not the whole indexing pipeline. Registration happens through
:func:`~murder.context_compiler.extraction.registry.default_registry` /
:func:`register_treesitter_extractors`.
"""

from __future__ import annotations

from murder.context_compiler.extraction.registry import (
    LANG_C,
    LANG_CPP,
    LANG_CSS,
    LANG_GO,
    LANG_HTML,
    LANG_JAVASCRIPT,
    LANG_JSX,
    LANG_RUST,
    LANG_TSX,
    LANG_TYPESCRIPT,
    ExtractorRegistry,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_C,
    GRAMMAR_CPP,
    GRAMMAR_CSS,
    GRAMMAR_GO,
    GRAMMAR_HTML,
    GRAMMAR_JAVASCRIPT,
    GRAMMAR_LESS,
    GRAMMAR_RUST,
    GRAMMAR_SCSS,
    GRAMMAR_TSX,
    GRAMMAR_TYPESCRIPT,
    GrammarStatus,
    get_language,
    get_parser,
    grammar_available,
    grammar_status,
    parse_source,
    reset_grammar_cache,
)
from murder.context_compiler.extraction.treesitter.c_family import CFamilyExtractor
from murder.context_compiler.extraction.treesitter.css import CssExtractor
from murder.context_compiler.extraction.treesitter.go import GoExtractor
from murder.context_compiler.extraction.treesitter.html import HtmlExtractor
from murder.context_compiler.extraction.treesitter.javascript import (
    JavaScriptExtractor,
    TypeScriptExtractor,
)
from murder.context_compiler.extraction.treesitter.rust import RustExtractor

_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")
_TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")
_C_EXTENSIONS = (".c", ".h")
_CPP_EXTENSIONS = (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx")
_CSS_EXTENSIONS = (".css", ".scss", ".sass", ".less")


def register_default_extractors(registry: ExtractorRegistry) -> None:
    """Register JS/JSX and TS/TSX tree-sitter extractors on ``registry``."""
    registry.register(
        JavaScriptExtractor(),
        languages=(LANG_JAVASCRIPT, LANG_JSX),
        extensions=_JS_EXTENSIONS,
        priority=10,
    )
    registry.register(
        TypeScriptExtractor(),
        languages=(LANG_TYPESCRIPT, LANG_TSX),
        extensions=_TS_EXTENSIONS,
        priority=10,
    )


def register_treesitter_extractors(registry: ExtractorRegistry) -> None:
    """Register all built-in tree-sitter language extractors (merge-friendly)."""
    register_default_extractors(registry)
    registry.register(
        RustExtractor(),
        languages=(LANG_RUST,),
        extensions=(".rs",),
        priority=10,
    )
    registry.register(
        CFamilyExtractor(),
        languages=(LANG_C, LANG_CPP),
        extensions=(*_C_EXTENSIONS, *_CPP_EXTENSIONS),
        priority=10,
    )
    registry.register(
        GoExtractor(),
        languages=(LANG_GO,),
        extensions=(".go",),
        priority=10,
    )
    registry.register(
        HtmlExtractor(),
        languages=(LANG_HTML,),
        extensions=(".html", ".htm"),
        priority=10,
    )
    registry.register(
        CssExtractor(),
        languages=(LANG_CSS,),
        extensions=_CSS_EXTENSIONS,
        priority=10,
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
    "CFamilyExtractor",
    "CssExtractor",
    "GoExtractor",
    "GrammarStatus",
    "HtmlExtractor",
    "JavaScriptExtractor",
    "RustExtractor",
    "TypeScriptExtractor",
    "get_language",
    "get_parser",
    "grammar_available",
    "grammar_status",
    "parse_source",
    "register_default_extractors",
    "register_treesitter_extractors",
    "reset_grammar_cache",
]
