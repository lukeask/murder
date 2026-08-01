"""Focused tests for tree-sitter JS/TS extraction invariants."""

from __future__ import annotations

import pytest

from murder.context_compiler.extraction.registry import (
    ExtractorRegistry,
    default_registry,
    reset_default_registry,
)
from murder.context_compiler.extraction.treesitter import (
    JavaScriptExtractor,
    TypeScriptExtractor,
    grammar_available,
    register_default_extractors,
    reset_grammar_cache,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_JAVASCRIPT,
    GRAMMAR_TSX,
    GRAMMAR_TYPESCRIPT,
)

_js_ready = grammar_available(GRAMMAR_JAVASCRIPT)
_ts_ready = grammar_available(GRAMMAR_TYPESCRIPT)
_tsx_ready = grammar_available(GRAMMAR_TSX)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

requires_js = pytest.mark.skipif(
    not _js_ready,
    reason="tree-sitter-javascript grammar unavailable",
)
requires_ts = pytest.mark.skipif(
    not _ts_ready,
    reason="tree-sitter-typescript grammar unavailable",
)
requires_tsx = pytest.mark.skipif(
    not _tsx_ready,
    reason="tree-sitter-typescript TSX grammar unavailable",
)


@pytest.fixture(autouse=True)
def _restore_registry() -> None:
    reset_default_registry()
    yield
    reset_default_registry()


def test_fail_soft_when_grammar_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_grammar_cache()

    def _boom() -> None:
        raise RuntimeError("simulated missing grammar")

    monkeypatch.setattr(
        "murder.context_compiler.extraction.treesitter.base._load_javascript",
        _boom,
    )
    reset_grammar_cache()
    extractor = JavaScriptExtractor()
    result = extractor.extract("src/a.js", "export function foo() {}")
    assert result.parse_status == "text_only"
    assert any(d.code == "grammar_unavailable" for d in result.diagnostics)
    assert result.semantic_units == ()
    # Other grammars must remain independently loadable.
    assert grammar_available(GRAMMAR_TYPESCRIPT) or not _ts_ready
    reset_grammar_cache()


@requires_js
def test_js_imports_exports_and_named_units() -> None:
    source = """
import foo from "./a";
import { bar as baz, qux } from "./b";
import * as ns from "./c";
import "./side";

export function named(x) {
  return helper(x);
}
export const arrow = (n) => n + 1;
export const VALUE = 42;

export class Widget extends Base {
  render() {
    return draw();
  }
}

function localFn() {
  [1, 2, 3].map((item) => item * 2);
}
"""
    result = JavaScriptExtractor().extract("src/mod.js", source)
    assert result.parse_status in {"parsed", "partial"}
    assert result.language == "javascript"

    kinds = {(u.language_kind, u.unqualified_name, u.exported) for u in result.semantic_units}
    assert ("function", "named", True) in kinds
    assert ("function", "arrow", True) in kinds
    assert ("constant", "VALUE", True) in kinds
    assert ("class", "Widget", True) in kinds
    assert ("method", "render", False) in kinds
    assert ("function", "localFn", False) in kinds

    # Anonymous map callback must not become its own semantic unit.
    arrow_units = [u for u in result.semantic_units if u.language_kind == "function"]
    assert all(u.unqualified_name != "item" for u in arrow_units)
    assert len([u for u in result.semantic_units if u.unqualified_name == "arrow"]) == 1

    import_specs = {
        (i.import_kind, i.module_specifier, i.imported_name, i.local_alias) for i in result.imports
    }
    assert ("default", "./a", "default", "foo") in import_specs
    assert ("named", "./b", "bar", "baz") in import_specs
    assert ("named", "./b", "qux", None) in import_specs
    assert ("namespace", "./c", "*", "ns") in import_specs
    assert ("side_effect", "./side", None, None) in import_specs

    rels = result.relationships
    assert any(r.relation_kind == "inherits" and r.target_qualified_name == "Base" for r in rels)
    assert any(r.relation_kind == "contains" for r in rels)
    assert any(r.relation_kind == "calls" and r.target_qualified_name == "helper" for r in rels)


@requires_ts
def test_ts_interfaces_types_enums_and_implements() -> None:
    source = """
import type { Foo } from "./types";
export interface Point { x: number; y: number; }
export type Id = string | number;
export enum Color { Red, Green }
export class Service extends Base implements Point {
  run(): void { act(); }
}
namespace Util {
  export function helper() {}
}
"""
    result = TypeScriptExtractor().extract("src/mod.ts", source)
    assert result.parse_status in {"parsed", "partial"}
    names = {(u.language_kind, u.unqualified_name, u.exported) for u in result.semantic_units}
    assert ("interface", "Point", True) in names
    assert ("type_alias", "Id", True) in names
    assert ("enum", "Color", True) in names
    assert ("class", "Service", True) in names
    assert ("method", "run", False) in names
    assert ("namespace", "Util", False) in names
    assert ("function", "helper", True) in names or ("function", "helper", False) in names

    assert any(
        i.import_kind == "type_only" and i.module_specifier == "./types" for i in result.imports
    )
    rels = result.relationships
    assert any(r.relation_kind == "inherits" and r.target_qualified_name == "Base" for r in rels)
    assert any(r.relation_kind == "implements" and r.target_qualified_name == "Point" for r in rels)


@requires_tsx
def test_tsx_named_component_not_jsx_callbacks() -> None:
    source = """
export function App() {
  return <div onClick={() => ping()} />;
}
const Title = () => <h1>Hi</h1>;
"""
    result = TypeScriptExtractor().extract("src/App.tsx", source)
    assert result.language == "tsx"
    assert result.parse_status in {"parsed", "partial"}
    fn_names = [u.unqualified_name for u in result.semantic_units if u.language_kind == "function"]
    assert "App" in fn_names
    assert "Title" in fn_names
    # onClick arrow must not be indexed
    assert all(name not in {"onClick"} for name in fn_names)
    assert len(fn_names) == len({"App", "Title"})


def test_default_registry_selects_js_and_ts() -> None:
    registry = default_registry()
    js = registry.select("pkg/a.mjs")
    assert js is not None
    assert js.base.extractor_id == "tree-sitter-javascript"
    assert js.extractor_version == "schema-1:tree-sitter-javascript-1"

    ts = registry.select("pkg/a.tsx")
    assert ts is not None
    assert ts.base.extractor_id == "tree-sitter-typescript"
    assert ts.extractor_version == "schema-1:tree-sitter-typescript-1"


def test_register_on_fresh_registry() -> None:
    registry = ExtractorRegistry()
    register_default_extractors(registry)
    pipe = registry.select("x.cts")
    assert pipe is not None
    assert pipe.language == "typescript"
    assert pipe.base.extractor_id == "tree-sitter-typescript"
