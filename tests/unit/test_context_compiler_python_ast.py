"""Focused tests for Context Assembler 2 Python AST extraction."""

from __future__ import annotations

from murder.context_compiler.extraction import (
    PythonAstExtractor,
    default_registry,
    reset_default_registry,
)
from murder.context_compiler.extraction.models import (
    REL_CALLS,
    REL_CONTAINS,
    REL_INHERITS,
)
from murder.context_compiler.extraction.python_ast import module_qualifier_from_path

SAMPLE = '''\
"""Sample module."""
from typing import TypeAlias
import os
from collections import defaultdict as dd

MAX_SIZE = 100
ConfigMap: TypeAlias = dict[str, int]
_hidden = 1

class Base:
    def ready(self) -> bool:
        return True

class Worker(Base):
    def run(self, items: list[str]) -> int:
        total = helper(items)
        def accumulate(values: list[int]) -> int:
            acc = 0
            for value in values:
                acc += value
            return acc
        return accumulate([total])

def helper(items: list[str]) -> int:
    return len(items)

async def fetch(url: str) -> str:
    return url
'''


def test_module_qualifier_from_path() -> None:
    assert module_qualifier_from_path("pkg/mod.py") == "pkg.mod"
    assert module_qualifier_from_path("pkg/__init__.py") == "pkg"
    assert module_qualifier_from_path("mod.py") == "mod"


def test_default_registry_selects_python_ast() -> None:
    reset_default_registry()
    registry = default_registry()
    pipe = registry.select("pkg/mod.py")
    assert pipe is not None
    assert pipe.base.extractor_id == "python-ast"
    assert pipe.extractor_version == "schema-1:python-ast-1"
    assert pipe.language == "python"


def test_qualified_names_parent_containment_imports_inheritance() -> None:
    extractor = PythonAstExtractor()
    result = extractor.extract("pkg/mod.py", SAMPLE)

    assert result.parse_status == "parsed"
    assert result.language == "python"

    by_qual = {u.qualified_name: u for u in result.semantic_units}

    assert "pkg.mod.helper" in by_qual
    assert by_qual["pkg.mod.helper"].language_kind == "function"
    assert by_qual["pkg.mod.helper"].exported is True
    assert by_qual["pkg.mod.helper"].signature is not None
    assert "def helper" in by_qual["pkg.mod.helper"].signature

    assert "pkg.mod.fetch" in by_qual
    assert by_qual["pkg.mod.fetch"].metadata.get("async") is True

    assert "pkg.mod.Base" in by_qual
    assert "pkg.mod.Worker" in by_qual
    worker = by_qual["pkg.mod.Worker"]
    assert worker.language_kind == "class"
    assert worker.metadata.get("bases") == ["Base"]

    run = by_qual["pkg.mod.Worker.run"]
    assert run.language_kind == "method"
    assert run.parent_local_id == worker.local_id

    # Nontrivial nested function keeps parent containment under the method.
    accumulate = by_qual["pkg.mod.Worker.run.accumulate"]
    assert accumulate.language_kind == "function"
    assert accumulate.parent_local_id == run.local_id
    assert accumulate.metadata.get("nested") is True

    assert "pkg.mod.MAX_SIZE" in by_qual
    assert by_qual["pkg.mod.MAX_SIZE"].language_kind == "constant"
    assert "pkg.mod.ConfigMap" in by_qual
    assert by_qual["pkg.mod.ConfigMap"].language_kind == "type_alias"
    assert not any(u.unqualified_name == "_hidden" for u in result.semantic_units)

    # Imports
    specs = {(i.module_specifier, i.imported_name, i.local_alias) for i in result.imports}
    assert ("os", None, None) in specs
    assert ("collections", "defaultdict", "dd") in specs
    assert ("typing", "TypeAlias", None) in specs

    # Inheritance + contains
    inherits = [
        r
        for r in result.relationships
        if r.relation_kind == REL_INHERITS and r.source_unit_local_id == worker.local_id
    ]
    assert len(inherits) == 1
    assert inherits[0].target_local_id == by_qual["pkg.mod.Base"].local_id
    assert inherits[0].target_qualified_name == "pkg.mod.Base"

    contains = {
        (r.source_unit_local_id, r.target_local_id)
        for r in result.relationships
        if r.relation_kind == REL_CONTAINS
    }
    assert (worker.local_id, run.local_id) in contains
    assert (run.local_id, accumulate.local_id) in contains

    # Local call helper(...) from Worker.run resolves.
    calls = [
        r
        for r in result.relationships
        if r.relation_kind == REL_CALLS
        and r.source_unit_local_id == run.local_id
        and r.target_local_id == by_qual["pkg.mod.helper"].local_id
    ]
    assert calls


def test_syntax_error_returns_failed_without_units() -> None:
    result = PythonAstExtractor().extract("broken.py", "def oops(\n")
    assert result.parse_status == "failed"
    assert result.semantic_units == ()
    assert any(d.code == "syntax_error" for d in result.diagnostics)
