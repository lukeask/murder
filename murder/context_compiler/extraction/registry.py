"""Language extractor registry: selection, enrichers, and version identity.

The registry selects a base :class:`LanguageExtractor` plus zero or more
:class:`FrameworkEnricher` adapters. Combined extractor versions follow::

    schema-1:python-ast-1
    schema-1:tree-sitter-typescript-1:react-1
    schema-1:vue-sfc-1

Concrete backends register via :meth:`ExtractorRegistry.register` and
:meth:`ExtractorRegistry.register_enricher`. Importing
``murder.context_compiler.extraction`` registers available backends (Python AST,
tree-sitter JS/TS, …) on the process-wide default registry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from murder.context_compiler.extraction.common import (
    EXTRACTION_SCHEMA_VERSION,
    build_extractor_version,
)
from murder.context_compiler.extraction.models import FileExtraction
from murder.context_compiler.extraction.protocols import (
    FrameworkEnricher,
    LanguageExtractor,
)

# Selection language keys used for extension / hint dispatch.
LANG_VUE = "vue"
LANG_SVELTE = "svelte"
LANG_TYPESCRIPT = "typescript"
LANG_TSX = "tsx"
LANG_JAVASCRIPT = "javascript"
LANG_JSX = "jsx"
LANG_PYTHON = "python"
LANG_RUST = "rust"
LANG_C = "c"
LANG_CPP = "cpp"
LANG_GO = "go"
LANG_HTML = "html"
LANG_CSS = "css"

# Extension → canonical language key. Precedence among overlapping families is
# encoded by checking more specific formats (.vue / .svelte) before generics.
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".vue": LANG_VUE,
    ".svelte": LANG_SVELTE,
    ".ts": LANG_TYPESCRIPT,
    ".tsx": LANG_TSX,
    ".mts": LANG_TYPESCRIPT,
    ".cts": LANG_TYPESCRIPT,
    ".js": LANG_JAVASCRIPT,
    ".jsx": LANG_JSX,
    ".mjs": LANG_JAVASCRIPT,
    ".cjs": LANG_JAVASCRIPT,
    ".py": LANG_PYTHON,
    ".pyi": LANG_PYTHON,
    ".rs": LANG_RUST,
    ".c": LANG_C,
    ".h": LANG_C,
    ".cc": LANG_CPP,
    ".cpp": LANG_CPP,
    ".cxx": LANG_CPP,
    ".hpp": LANG_CPP,
    ".hh": LANG_CPP,
    ".hxx": LANG_CPP,
    ".go": LANG_GO,
    ".html": LANG_HTML,
    ".htm": LANG_HTML,
    ".css": LANG_CSS,
    ".scss": LANG_CSS,
    ".sass": LANG_CSS,
    ".less": LANG_CSS,
}

# Language-hint aliases → canonical key.
LANGUAGE_HINT_ALIASES: dict[str, str] = {
    "vue": LANG_VUE,
    "svelte": LANG_SVELTE,
    "typescript": LANG_TYPESCRIPT,
    "ts": LANG_TYPESCRIPT,
    "tsx": LANG_TSX,
    "javascript": LANG_JAVASCRIPT,
    "js": LANG_JAVASCRIPT,
    "jsx": LANG_JSX,
    "python": LANG_PYTHON,
    "py": LANG_PYTHON,
    "rust": LANG_RUST,
    "rs": LANG_RUST,
    "c": LANG_C,
    "c++": LANG_CPP,
    "cpp": LANG_CPP,
    "cxx": LANG_CPP,
    "go": LANG_GO,
    "golang": LANG_GO,
    "html": LANG_HTML,
    "css": LANG_CSS,
    "scss": LANG_CSS,
    "sass": LANG_CSS,
    "less": LANG_CSS,
}

# Framework enrichers commonly considered for a base language.
_DEFAULT_ENRICHER_CANDIDATES: dict[str, tuple[str, ...]] = {
    LANG_TYPESCRIPT: ("react", "angular"),
    LANG_TSX: ("react", "angular"),
    LANG_JAVASCRIPT: ("react",),
    LANG_JSX: ("react",),
}


@dataclass(frozen=True, slots=True)
class ExtractionPipeline:
    """Selected base extractor, applicable enrichers, and combined version."""

    language: str
    base: LanguageExtractor
    enrichers: tuple[FrameworkEnricher, ...]
    extractor_version: str
    schema_version: str = EXTRACTION_SCHEMA_VERSION

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        """Run base extraction then each enricher in registration order."""
        result = self.base.extract(path, source, language_hint=language_hint)
        for enricher in self.enrichers:
            result = enricher.enrich(result, source, path=path)
        return result


@dataclass
class _ExtractorEntry:
    extractor: LanguageExtractor
    languages: frozenset[str]
    extensions: frozenset[str]
    filenames: frozenset[str]
    priority: int = 0


@dataclass
class _EnricherEntry:
    enricher: FrameworkEnricher
    enricher_id: str
    languages: frozenset[str]
    extensions: frozenset[str]
    priority: int = 0


@dataclass
class ExtractorRegistry:
    """Mutable registry of base extractors and framework enrichers.

    Selection order:

    1. Exact basename match against registered filenames.
    2. Extension → language key (``.vue`` / ``.svelte`` before generic JS/TS).
    3. Normalized ``language_hint`` when extension does not resolve.
    4. Among extractors claiming that language/extension, highest ``priority``,
       then ``extractor_id`` for determinism.
    """

    schema_version: str = EXTRACTION_SCHEMA_VERSION
    _extractors: list[_ExtractorEntry] = field(default_factory=list)
    _enrichers: list[_EnricherEntry] = field(default_factory=list)

    def register(
        self,
        extractor: LanguageExtractor,
        *,
        languages: Sequence[str] = (),
        extensions: Sequence[str] = (),
        filenames: Sequence[str] = (),
        priority: int = 0,
    ) -> None:
        """Register a base language / format extractor.

        At least one of ``languages``, ``extensions``, or ``filenames`` should
        be provided so the extractor can be selected.
        """
        lang_keys = frozenset(_normalize_language(x) for x in languages if x)
        ext_keys = frozenset(_normalize_extension(x) for x in extensions if x)
        name_keys = frozenset(x.strip() for x in filenames if x and x.strip())
        # Also map extensions through the canonical table so registering
        # ``extensions={".py"}`` selects under language ``python``.
        for ext in ext_keys:
            mapped = EXTENSION_TO_LANGUAGE.get(ext)
            if mapped:
                lang_keys = lang_keys | {mapped}
        self._extractors.append(
            _ExtractorEntry(
                extractor=extractor,
                languages=lang_keys,
                extensions=ext_keys,
                filenames=name_keys,
                priority=priority,
            )
        )

    def register_enricher(
        self,
        enricher: FrameworkEnricher,
        *,
        languages: Sequence[str] = (),
        extensions: Sequence[str] = (),
        priority: int = 0,
    ) -> None:
        """Register a framework enricher (React, Vue, Angular, Svelte, …)."""
        lang_keys = frozenset(_normalize_language(x) for x in languages if x)
        ext_keys = frozenset(_normalize_extension(x) for x in extensions if x)
        for ext in ext_keys:
            mapped = EXTENSION_TO_LANGUAGE.get(ext)
            if mapped:
                lang_keys = lang_keys | {mapped}
        self._enrichers.append(
            _EnricherEntry(
                enricher=enricher,
                enricher_id=enricher.enricher_id,
                languages=lang_keys,
                extensions=ext_keys,
                priority=priority,
            )
        )

    def clear(self) -> None:
        """Remove all registered extractors and enrichers."""
        self._extractors.clear()
        self._enrichers.clear()

    def resolve_language(
        self,
        path: str,
        language_hint: str | None = None,
    ) -> str | None:
        """Map path / hint to a canonical language key, if known."""
        basename = PurePosixPath(path.replace("\\", "/")).name
        for entry in self._extractors:
            if basename in entry.filenames:
                if entry.languages:
                    return sorted(entry.languages)[0]
                return basename

        extension = _path_extension(path)
        if extension and extension in EXTENSION_TO_LANGUAGE:
            return EXTENSION_TO_LANGUAGE[extension]

        if language_hint:
            return _normalize_language(language_hint) or None
        return None

    def select(
        self,
        path: str,
        *,
        language_hint: str | None = None,
        source: str | None = None,
    ) -> ExtractionPipeline | None:
        """Select base + enrichers + combined version for ``path``.

        Returns ``None`` when no registered base extractor matches. Framework
        enrichers are included only when ``source`` is provided and
        :meth:`FrameworkEnricher.applies` returns true (or when ``source`` is
        omitted, enrichers that claim the language/extension unconditionally
        via registration metadata are *not* auto-applied — callers should pass
        ``source`` for detection).
        """
        base_entry = self._select_base_entry(path, language_hint=language_hint)
        if base_entry is None:
            return None

        language = self.resolve_language(path, language_hint) or ""
        if not language and base_entry.languages:
            language = sorted(base_entry.languages)[0]

        enrichers = self._select_enrichers(
            path,
            language=language,
            language_hint=language_hint,
            source=source,
        )
        version = build_extractor_version(
            self.schema_version,
            base_entry.extractor.extractor_version,
            *(e.enricher_version for e in enrichers),
        )
        return ExtractionPipeline(
            language=language,
            base=base_entry.extractor,
            enrichers=enrichers,
            extractor_version=version,
            schema_version=self.schema_version,
        )

    def _select_base_entry(
        self,
        path: str,
        *,
        language_hint: str | None,
    ) -> _ExtractorEntry | None:
        basename = PurePosixPath(path.replace("\\", "/")).name
        extension = _path_extension(path)
        language = self.resolve_language(path, language_hint)

        candidates: list[_ExtractorEntry] = []
        for entry in self._extractors:
            if basename in entry.filenames:
                candidates.append(entry)
                continue
            if extension and extension in entry.extensions:
                candidates.append(entry)
                continue
            if language and language in entry.languages:
                candidates.append(entry)
                continue
            if entry.extractor.supports(path, language_hint):
                candidates.append(entry)

        if not candidates:
            return None

        # Prefer filename matches, then extension matches, then language, then supports().
        def rank(entry: _ExtractorEntry) -> tuple[int, int, str]:
            if basename in entry.filenames:
                tier = 0
            elif extension and extension in entry.extensions:
                tier = 1
            elif language and language in entry.languages:
                tier = 2
            else:
                tier = 3
            return (tier, -entry.priority, entry.extractor.extractor_id)

        return min(candidates, key=rank)

    def _select_enrichers(
        self,
        path: str,
        *,
        language: str,
        language_hint: str | None,
        source: str | None,
    ) -> tuple[FrameworkEnricher, ...]:
        extension = _path_extension(path)
        preferred_ids = _DEFAULT_ENRICHER_CANDIDATES.get(language, ())

        matched: list[tuple[int, int, str, FrameworkEnricher]] = []
        for index, entry in enumerate(self._enrichers):
            claimed = False
            if extension and extension in entry.extensions:
                claimed = True
            if language and language in entry.languages:
                claimed = True
            if preferred_ids and entry.enricher_id in preferred_ids:
                claimed = True
            if not claimed and not entry.languages and not entry.extensions:
                # Globally registered enricher — always consider applies().
                claimed = True
            if not claimed:
                continue

            if source is None:
                # Without source, only apply enrichers whose registration is
                # exclusive to this path's format (e.g. .vue → vue enricher),
                # not speculative React/Angular detection.
                if extension and extension in entry.extensions and not preferred_ids:
                    matched.append((entry.priority, index, entry.enricher_id, entry.enricher))
                elif language in {LANG_VUE, LANG_SVELTE} and language in entry.languages:
                    matched.append((entry.priority, index, entry.enricher_id, entry.enricher))
                continue

            if entry.enricher.applies(
                path,
                source,
                language=language or None,
                language_hint=language_hint,
            ):
                matched.append((entry.priority, index, entry.enricher_id, entry.enricher))

        # Stable: higher priority first, then registration order, then id.
        matched.sort(key=lambda item: (-item[0], item[1], item[2]))
        # Deduplicate by enricher_id, keeping first (highest priority).
        seen: set[str] = set()
        result: list[FrameworkEnricher] = []
        for _priority, _index, enricher_id, enricher in matched:
            if enricher_id in seen:
                continue
            seen.add(enricher_id)
            result.append(enricher)
        return tuple(result)


def _normalize_extension(extension: str) -> str:
    text = extension.strip().lower()
    if not text:
        return text
    return text if text.startswith(".") else f".{text}"


def _normalize_language(language: str) -> str:
    key = language.strip().lower()
    if not key:
        return key
    return LANGUAGE_HINT_ALIASES.get(key, key)


def _path_extension(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).name
    if name.startswith(".") and name.count(".") == 1:
        # Dotfiles like ``.env`` have no language extension.
        return ""
    suffix = PurePosixPath(name).suffix.lower()
    return suffix


_DEFAULT_REGISTRY = ExtractorRegistry()
_BUILTIN_EXTRACTORS_REGISTERED = False


def _ensure_builtin_extractors(registry: ExtractorRegistry) -> None:
    """Idempotently register built-in language extractors on ``registry``."""
    global _BUILTIN_EXTRACTORS_REGISTERED  # noqa: PLW0603
    if registry is _DEFAULT_REGISTRY and _BUILTIN_EXTRACTORS_REGISTERED:
        return
    # Local imports keep registry importable before language modules load.
    from murder.context_compiler.extraction.frameworks import (  # noqa: PLC0415
        register_framework_adapters,
    )
    from murder.context_compiler.extraction.python_ast import (  # noqa: PLC0415
        register_python_extractor,
    )
    from murder.context_compiler.extraction.treesitter import (  # noqa: PLC0415
        register_treesitter_extractors,
    )

    register_python_extractor(registry)
    register_treesitter_extractors(registry)
    register_framework_adapters(registry)
    if registry is _DEFAULT_REGISTRY:
        _BUILTIN_EXTRACTORS_REGISTERED = True


def default_registry() -> ExtractorRegistry:
    """Process-wide registry with built-in extractors (Python AST, JS/TS, frameworks, …)."""
    _ensure_builtin_extractors(_DEFAULT_REGISTRY)
    return _DEFAULT_REGISTRY


def reset_default_registry() -> ExtractorRegistry:
    """Clear, re-register builtins, and return the process-wide default registry."""
    global _BUILTIN_EXTRACTORS_REGISTERED  # noqa: PLW0603
    _DEFAULT_REGISTRY.clear()
    _BUILTIN_EXTRACTORS_REGISTERED = False
    _ensure_builtin_extractors(_DEFAULT_REGISTRY)
    return _DEFAULT_REGISTRY


__all__ = [
    "EXTENSION_TO_LANGUAGE",
    "EXTRACTION_SCHEMA_VERSION",
    "LANGUAGE_HINT_ALIASES",
    "LANG_C",
    "LANG_CPP",
    "LANG_CSS",
    "LANG_GO",
    "LANG_HTML",
    "LANG_JAVASCRIPT",
    "LANG_JSX",
    "LANG_PYTHON",
    "LANG_RUST",
    "LANG_SVELTE",
    "LANG_TSX",
    "LANG_TYPESCRIPT",
    "LANG_VUE",
    "ExtractionPipeline",
    "ExtractorRegistry",
    "build_extractor_version",
    "default_registry",
    "reset_default_registry",
]
