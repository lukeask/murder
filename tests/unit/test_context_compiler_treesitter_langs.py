"""Focused tests for Rust/C/C++/Go/HTML/CSS tree-sitter extractors."""

from __future__ import annotations

import pytest

from murder.context_compiler.extraction.registry import (
    ExtractorRegistry,
    default_registry,
    reset_default_registry,
)
from murder.context_compiler.extraction.treesitter import (
    CFamilyExtractor,
    CssExtractor,
    GoExtractor,
    HtmlExtractor,
    RustExtractor,
    grammar_available,
    register_treesitter_extractors,
    reset_grammar_cache,
)
from murder.context_compiler.extraction.treesitter.base import (
    GRAMMAR_C,
    GRAMMAR_CPP,
    GRAMMAR_CSS,
    GRAMMAR_GO,
    GRAMMAR_HTML,
    GRAMMAR_RUST,
)

_rust = grammar_available(GRAMMAR_RUST)
_c = grammar_available(GRAMMAR_C)
_cpp = grammar_available(GRAMMAR_CPP)
_go = grammar_available(GRAMMAR_GO)
_html = grammar_available(GRAMMAR_HTML)
_css = grammar_available(GRAMMAR_CSS)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

requires_rust = pytest.mark.skipif(not _rust, reason="tree-sitter-rust unavailable")
requires_c = pytest.mark.skipif(not _c, reason="tree-sitter-c unavailable")
requires_cpp = pytest.mark.skipif(not _cpp, reason="tree-sitter-cpp unavailable")
requires_go = pytest.mark.skipif(not _go, reason="tree-sitter-go unavailable")
requires_html = pytest.mark.skipif(not _html, reason="tree-sitter-html unavailable")
requires_css = pytest.mark.skipif(not _css, reason="tree-sitter-css unavailable")


@pytest.fixture(autouse=True)
def _restore_registry() -> None:
    reset_default_registry()
    yield
    reset_default_registry()


def test_register_treesitter_extractors_is_merge_friendly() -> None:
    registry = ExtractorRegistry()
    register_treesitter_extractors(registry)
    pipe = registry.select("src/lib.rs")
    assert pipe is not None
    assert pipe.base.extractor_id == "tree-sitter-rust"
    assert registry.select("main.go") is not None
    assert registry.select("index.html") is not None
    assert registry.select("app.css") is not None
    assert registry.select("foo.c") is not None
    # Default registry also includes these.
    dr = default_registry()
    assert dr.select("src/lib.rs") is not None


def test_fail_soft_rust_grammar_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_grammar_cache()

    def _boom() -> None:
        raise RuntimeError("simulated missing grammar")

    monkeypatch.setattr(
        "murder.context_compiler.extraction.treesitter.base._load_rust",
        _boom,
    )
    reset_grammar_cache()
    result = RustExtractor().extract("src/lib.rs", "pub fn foo() {}")
    assert result.parse_status == "text_only"
    assert any(d.code == "grammar_unavailable" for d in result.diagnostics)
    # Unrelated grammars remain independently loadable.
    assert grammar_available(GRAMMAR_GO) or not _go
    reset_grammar_cache()


@requires_rust
def test_rust_units_imports_impls_and_calls() -> None:
    source = """
use std::collections::HashMap;
use crate::util::{Helper, tool as t};

pub struct Point { pub x: f64 }
pub trait Drawable { fn draw(&self); }
pub type Alias = i32;

impl Point {
    pub fn new(x: f64) -> Self { Self { x } }
}

impl Drawable for Point {
    fn draw(&self) { helper(); }
}

pub fn helper() {
    let p = Point::new(1.0);
    p.draw();
}
"""
    result = RustExtractor().extract("src/lib.rs", source)
    assert result.parse_status in {"parsed", "partial"}
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("struct", "Point") in kinds
    assert ("trait", "Drawable") in kinds
    assert ("type_alias", "Alias") in kinds
    assert ("function", "helper") in kinds
    assert ("method", "new") in kinds
    assert ("method", "draw") in kinds
    assert any(u.language_kind == "impl" for u in result.semantic_units)
    assert any(i.module_specifier for i in result.imports)
    assert any(r.relation_kind == "calls" for r in result.relationships)
    assert any(r.relation_kind == "implements" for r in result.relationships)


@requires_c
def test_c_functions_includes_and_typedefs() -> None:
    source = """
#include <stdio.h>
#include "local.h"

typedef struct Point { int x; } Point;
enum Color { RED, GREEN };

void foo(int x) { bar(x); }
int main(void) { foo(1); return 0; }
"""
    result = CFamilyExtractor().extract("src/main.c", source)
    assert result.parse_status in {"parsed", "partial"}
    assert result.language == "c"
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("function", "foo") in kinds
    assert ("function", "main") in kinds
    assert ("enum", "Color") in kinds
    assert ("typedef", "Point") in kinds or ("struct", "Point") in kinds
    specs = {i.module_specifier for i in result.imports}
    assert "stdio.h" in specs
    assert "local.h" in specs


@requires_cpp
def test_cpp_class_namespace_inheritance() -> None:
    source = """
#include <vector>
namespace ns {
class Base { public: virtual void v(); };
class Derived : public Base {
public:
  void v() override { helper(); }
};
using Alias = int;
}
"""
    result = CFamilyExtractor().extract("src/x.cpp", source)
    assert result.parse_status in {"parsed", "partial"}
    assert result.language == "cpp"
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("namespace", "ns") in kinds
    assert ("class", "Base") in kinds
    assert ("class", "Derived") in kinds
    assert ("type_alias", "Alias") in kinds
    assert any(r.relation_kind == "inherits" for r in result.relationships)


@requires_go
def test_go_methods_receivers_and_imports() -> None:
    source = """
package main
import (
  "fmt"
  f "fmt"
)
type Point struct { X int }
type Drawer interface { Draw() }
type Alias = int
func (p *Point) Dist() float64 { return float64(p.X) }
func Helper(x int) int { return x }
func main() {
  p := Point{X: 1}
  p.Dist()
  Helper(2)
  fmt.Println(p)
}
"""
    result = GoExtractor().extract("main.go", source)
    assert result.parse_status in {"parsed", "partial"}
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("struct", "Point") in kinds
    assert ("interface", "Drawer") in kinds
    assert ("type_alias", "Alias") in kinds
    assert ("function", "Helper") in kinds
    assert ("method", "Dist") in kinds
    dist = next(u for u in result.semantic_units if u.unqualified_name == "Dist")
    assert dist.qualified_name.endswith("Point.Dist") or "Point" in dist.qualified_name
    assert dist.metadata.get("receiver") == "Point"
    assert any(i.module_specifier == "fmt" for i in result.imports)
    assert any(r.relation_kind == "calls" for r in result.relationships)


@requires_html
def test_html_selective_entities_not_every_tag() -> None:
    source = """
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="/app.css">
  <script src="/app.js"></script>
  <template id="user-card"><slot name="title"></slot></template>
</head>
<body>
  <div id="app" class="root">
    <span>hello</span>
    <my-widget></my-widget>
    <UserProfile></UserProfile>
  </div>
</body>
</html>
"""
    result = HtmlExtractor().extract("index.html", source)
    assert result.parse_status in {"parsed", "partial"}
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("custom_element", "my-widget") in kinds
    assert ("component", "UserProfile") in kinds
    assert ("template", "user-card") in kinds
    assert ("slot", "title") in kinds
    assert ("element_id", "app") in kinds
    # Ordinary tags must not explode into units.
    assert not any(u.unqualified_name == "span" for u in result.semantic_units)
    assert not any(u.unqualified_name == "div" for u in result.semantic_units)
    specs = {i.module_specifier for i in result.imports}
    assert "/app.css" in specs
    assert "/app.js" in specs


@requires_css
def test_css_named_entities_not_every_selector() -> None:
    source = """
@import url("base.css");
@layer utilities;
:root {
  --color-primary: #333;
}
@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
.ordinary { color: red; }
.another { margin: 0; }
"""
    result = CssExtractor().extract("app.css", source)
    assert result.parse_status in {"parsed", "partial"}
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("keyframes", "fade-in") in kinds
    assert ("custom_property", "--color-primary") in kinds
    assert ("layer", "utilities") in kinds
    # Ordinary class selectors must not become units in plain CSS.
    assert ("css_module_class", "ordinary") not in kinds
    assert not any(u.unqualified_name == "ordinary" for u in result.semantic_units)
    assert any(i.module_specifier == "base.css" for i in result.imports)


@requires_css
def test_css_module_exports_classes() -> None:
    source = """
.button { color: blue; }
.label { font-weight: bold; }
"""
    result = CssExtractor().extract("Button.module.css", source)
    kinds = {(u.language_kind, u.unqualified_name) for u in result.semantic_units}
    assert ("css_module_class", "button") in kinds
    assert ("css_module_class", "label") in kinds


def test_sass_indented_text_only() -> None:
    result = CssExtractor().extract(
        "theme.sass",
        "body\n  color: red\n",
    )
    assert result.parse_status == "text_only"
    assert any(d.code == "sass_indented_unsupported" for d in result.diagnostics)
