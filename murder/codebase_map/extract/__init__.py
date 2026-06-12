"""Per-language symbol extraction.

A programmatic extractor enumerates the symbols a source file defines
(faithful signatures from the parse tree); the LLM only *describes* them.
Languages without an extractor fall back to pure-LLM (``extract_symbols``
returns ``None``).
"""

from __future__ import annotations

from murder.codebase_map.extract.base import Symbol, SymbolExtractor, extract_symbols

__all__ = ["Symbol", "SymbolExtractor", "extract_symbols"]
