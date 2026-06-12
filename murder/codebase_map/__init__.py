"""Codebase map — a cheap-LLM, commit-fresh repo digest.

See ``.murder/plans/codebase-map.md`` for the full design. This package
holds the symbol-extraction atom (:mod:`murder.codebase_map.extract`) and
the per-file LLM summarizer (:mod:`murder.codebase_map.summarize`).
"""

from __future__ import annotations

from murder.codebase_map.extract import Symbol, SymbolExtractor, extract_symbols

__all__ = ["Symbol", "SymbolExtractor", "extract_symbols"]
