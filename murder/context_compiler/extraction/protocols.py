"""Extractor and framework-enricher protocols.

Extractors and enrichers operate on one source file at a time. They must not
open SQLite, mutate snapshots, read Murder runtime state, call LLMs, render
recipient prompts, or depend on agent roles.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from murder.context_compiler.extraction.models import FileExtraction


@runtime_checkable
class LanguageExtractor(Protocol):
    """Structural extractor for one language / file format backend."""

    @property
    def extractor_id(self) -> str:
        """Stable backend identity, e.g. ``python-ast`` or ``tree-sitter-typescript``."""
        ...

    @property
    def extractor_version(self) -> str:
        """Backend identity plus version, e.g. ``python-ast-1``.

        Must change when extraction semantics change. Do not rely only on
        installed library package versions. Schema prefix is applied by the
        registry when building the combined version string.
        """
        ...

    def supports(self, path: str, language_hint: str | None = None) -> bool:
        """Return whether this extractor can handle ``path`` / hint."""
        ...

    def extract(
        self,
        path: str,
        source: str,
        *,
        language_hint: str | None = None,
    ) -> FileExtraction:
        """Extract normalized records from one source file."""
        ...


@runtime_checkable
class FrameworkEnricher(Protocol):
    """Optional post-pass that adds framework roles and relationships.

    Enrichers receive a base :class:`FileExtraction` and return a new immutable
    extraction (typically with updated ``semantic_role`` values, resource links,
    and ``renders_component`` / template relationships).
    """

    @property
    def enricher_id(self) -> str:
        """Stable adapter identity, e.g. ``react`` or ``angular``."""
        ...

    @property
    def enricher_version(self) -> str:
        """Adapter identity plus version, e.g. ``react-1``."""
        ...

    def applies(
        self,
        path: str,
        source: str,
        *,
        language: str | None = None,
        language_hint: str | None = None,
    ) -> bool:
        """Return whether this enricher should run for the file."""
        ...

    def enrich(
        self,
        extraction: FileExtraction,
        source: str,
        *,
        path: str,
    ) -> FileExtraction:
        """Return an enriched copy of ``extraction``."""
        ...


__all__ = [
    "FrameworkEnricher",
    "LanguageExtractor",
]
