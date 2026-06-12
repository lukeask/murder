"""Ground-truth tests for the Python AST symbol extractor (t057)."""

from __future__ import annotations

from murder.codebase_map.extract import Symbol, extract_symbols

_MODULE = '''\
"""Module docstring."""

MAX_RETRIES = 3

timeout: float = 1.5

_SECRET = "skip-me"

_internal: int = 7


def top_level(x: int, *args, flag: bool = False, **kwargs) -> str:
    """Do a thing.

    Long description.
    """
    return str(x)


async def fetch(url: str) -> bytes:
    """Fetch the url."""
    return b""


def _helper(a, b=1):
    """Private but real."""
    return a + b


class Widget(Base):
    """A widget."""

    def render(self, depth: int = 0) -> str:
        """Render it."""
        return ""

    async def reload(self):
        return None
'''


def _by_name(symbols: list[Symbol]) -> dict[str, Symbol]:
    return {s.name: s for s in symbols}


def test_extracts_expected_symbols_in_source_order():
    symbols = extract_symbols("mod.py", _MODULE)
    assert symbols is not None
    # Source order by lineno.
    linenos = [s.lineno for s in symbols]
    assert linenos == sorted(linenos)
    names = [s.name for s in symbols]
    assert names == [
        "MAX_RETRIES",
        "timeout",
        "top_level",
        "fetch",
        "_helper",
        "Widget",
        "Widget.render",
        "Widget.reload",
    ]


def test_constants():
    by_name = _by_name(extract_symbols("mod.py", _MODULE) or [])
    assert by_name["MAX_RETRIES"].kind == "constant"
    assert by_name["MAX_RETRIES"].signature == "MAX_RETRIES"
    assert by_name["timeout"].kind == "constant"
    assert by_name["timeout"].signature == "timeout: float"


def test_private_constant_skipped_private_function_kept():
    names = [s.name for s in (extract_symbols("mod.py", _MODULE) or [])]
    assert "_SECRET" not in names
    assert "_internal" not in names
    assert "_helper" in names  # private functions are real symbols


def test_function_signature_and_docstring():
    by_name = _by_name(extract_symbols("mod.py", _MODULE) or [])
    fn = by_name["top_level"]
    assert fn.kind == "function"
    assert fn.signature == (
        "def top_level(x: int, *args, flag: bool = ..., **kwargs) -> str"
    )
    assert fn.docstring == "Do a thing."


def test_async_function():
    by_name = _by_name(extract_symbols("mod.py", _MODULE) or [])
    fn = by_name["fetch"]
    assert fn.kind == "function"
    assert fn.signature == "async def fetch(url: str) -> bytes"
    assert fn.docstring == "Fetch the url."


def test_class_and_methods():
    by_name = _by_name(extract_symbols("mod.py", _MODULE) or [])
    cls = by_name["Widget"]
    assert cls.kind == "class"
    assert cls.signature == "class Widget(Base)"
    assert cls.docstring == "A widget."

    render = by_name["Widget.render"]
    assert render.kind == "method"
    assert render.signature == "def render(self, depth: int = ...) -> str"
    assert render.docstring == "Render it."

    reload = by_name["Widget.reload"]
    assert reload.kind == "method"
    assert reload.signature == "async def reload(self)"
    assert reload.docstring is None


def test_syntax_error_returns_empty_list():
    result = extract_symbols("broken.py", "def oops(:\n")
    assert result == []


def test_non_python_returns_none():
    assert extract_symbols("foo.rs", "fn main() {}") is None
