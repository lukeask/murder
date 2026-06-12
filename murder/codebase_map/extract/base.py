"""Symbol contract and extension dispatch.

``Symbol`` is the flat, language-agnostic unit the map describes. An
extractor enumerates the symbols a single source file defines; the LLM
summarizer (t058) then describes them faithfully.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Symbol:
    """One named definition in a source file."""

    kind: str  # "function" | "class" | "method" | "constant"
    name: str  # qualified within the file, e.g. "Foo.bar" for a method
    signature: str  # faithful source signature, e.g. "def bar(self, x: int) -> str"
    lineno: int  # 1-based line where the symbol is defined
    docstring: str | None  # first line of the docstring if present, else None


class SymbolExtractor(Protocol):
    """Backend that enumerates the symbols a source file defines."""

    def extract(self, path: str, src: str) -> list[Symbol]:
        """Return symbols defined in ``src`` (``path`` is metadata only)."""
        ...


def extract_symbols(path: str, src: str) -> list[Symbol] | None:
    """Return symbols for a file we have an extractor for, else ``None``.

    ``None`` (not ``[]``) is the signal "no programmatic extractor" — the
    caller falls back to pure-LLM summarization. ``[]`` means "we have an
    extractor and the file defines zero symbols."
    """

    ext = os.path.splitext(path)[1].lower()
    if ext == ".py":
        from murder.codebase_map.extract.python_ast import PythonAstExtractor

        return PythonAstExtractor().extract(path, src)
    return None
