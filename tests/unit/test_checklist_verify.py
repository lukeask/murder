"""Anti-faking checklist verification (`murder/enforcement/checklist_verify.py`)."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from murder.enforcement.checklist_verify import (
    extract_citations,
    format_report,
    is_stub_file,
    is_stub_symbol,
    looks_like_code_work,
    verify_checklist,
    verify_item_text,
)

# --- helpers ----------------------------------------------------------------


def _seed_ticket(conn: sqlite3.Connection, ticket_id: str = "t001") -> None:
    conn.execute(
        "INSERT INTO tickets(id, title, wave, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ticket_id, "test ticket", 0, "in_progress", "2026-01-01", "2026-01-01"),
    )


def _add_item(
    conn: sqlite3.Connection,
    ticket_id: str,
    ord_: int,
    text: str,
    done: bool,
) -> None:
    conn.execute(
        "INSERT INTO checklist(ticket_id, ord, text, done, done_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (ticket_id, ord_, text, 1 if done else 0, "2026-01-01" if done else None),
    )


def _write(repo: Path, rel: str, body: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return p


# --- citation extraction ----------------------------------------------------


def test_extract_citations_path_only(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/foo.py", "x = 1\n")
    refs = extract_citations("touched `pkg/foo.py` today", tmp_path)
    assert len(refs) == 1
    assert refs[0].path == Path("pkg/foo.py")
    assert refs[0].symbol is None


def test_extract_citations_path_colon_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/foo.py", "def bar(): return 1\n")
    refs = extract_citations("see `pkg/foo.py:bar`", tmp_path)
    assert len(refs) == 1
    assert refs[0].symbol == "bar"


def test_extract_citations_pytest_style(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/foo.py", "class Bar: pass\n")
    refs = extract_citations("see `pkg/foo.py::Bar`", tmp_path)
    assert refs[0].symbol == "Bar"


def test_extract_citations_dotted_resolves_to_file(tmp_path: Path) -> None:
    _write(tmp_path, "murder/bus.py", "class Bus:\n    def publish(self): return 1\n")
    refs = extract_citations("calls murder.bus.Bus.publish to fan out", tmp_path)
    # Should resolve to bus.py with symbol Bus.publish.
    assert any(
        r.path == Path("murder/bus.py") and r.symbol == "Bus.publish" for r in refs
    )


def test_extract_citations_ignores_prose_backticks(tmp_path: Path) -> None:
    refs = extract_citations("the `cool` and `nice` words", tmp_path)
    assert refs == []


def test_extract_citations_dedupes(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    refs = extract_citations("`a.py` and `a.py` again", tmp_path)
    assert len(refs) == 1


# --- stub detection ---------------------------------------------------------


def test_is_stub_file_empty(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", "")
    stub, _ = is_stub_file(p)
    assert stub


def test_is_stub_file_only_pass(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", "pass\n")
    stub, _ = is_stub_file(p)
    assert stub


def test_is_stub_file_only_imports_and_stub_funcs(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "x.py",
        """
        from __future__ import annotations
        import sys

        def todo():
            raise NotImplementedError
        """,
    )
    stub, _ = is_stub_file(p)
    assert stub


def test_is_stub_file_real_implementation(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "x.py",
        """
        def add(a, b):
            return a + b
        """,
    )
    stub, _ = is_stub_file(p)
    assert not stub


def test_is_stub_symbol_function_with_pass(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", "def foo():\n    pass\n")
    stub, reason = is_stub_symbol(p, "foo")
    assert stub
    assert "stub body" in reason


def test_is_stub_symbol_function_real(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", "def foo():\n    return 42\n")
    stub, _ = is_stub_symbol(p, "foo")
    assert not stub


def test_is_stub_symbol_method(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "x.py",
        """
        class Bar:
            def parse(self):
                raise NotImplementedError("soon")
            def real(self):
                return 1
        """,
    )
    stub_parse, _ = is_stub_symbol(p, "Bar.parse")
    stub_real, _ = is_stub_symbol(p, "Bar.real")
    assert stub_parse
    assert not stub_real


def test_is_stub_symbol_missing(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", "def foo(): return 1\n")
    stub, reason = is_stub_symbol(p, "nope")
    assert stub
    assert "not found" in reason


def test_is_stub_symbol_docstring_only_is_stub(tmp_path: Path) -> None:
    p = _write(tmp_path, "x.py", 'def foo():\n    """todo"""\n')
    stub, _ = is_stub_symbol(p, "foo")
    assert stub


# --- verify_item_text -------------------------------------------------------


def test_verify_item_text_flags_missing_path(tmp_path: Path) -> None:
    _, issues = verify_item_text(
        "implemented `pkg/missing.py:foo`", tmp_path, require_citation=True
    )
    assert any("does not exist" in i for i in issues)


def test_verify_item_text_flags_path_escaping_repo(tmp_path: Path) -> None:
    _, issues = verify_item_text(
        "implemented `../../etc/passwd.py`", tmp_path, require_citation=True
    )
    assert any("escapes repo root" in i for i in issues)


def test_verify_item_text_flags_stub_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/foo.py", "def bar():\n    pass\n")
    _, issues = verify_item_text(
        "implemented `pkg/foo.py:bar`", tmp_path, require_citation=True
    )
    assert any("stub body" in i for i in issues)


def test_verify_item_text_passes_real_symbol(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/foo.py", "def bar():\n    return 1\n")
    _, issues = verify_item_text(
        "implemented `pkg/foo.py:bar`", tmp_path, require_citation=True
    )
    assert issues == []


def test_verify_item_text_requires_citation_when_codey(tmp_path: Path) -> None:
    _, issues = verify_item_text(
        "implement Foo.parse", tmp_path, require_citation=True
    )
    assert any("no file citation" in i for i in issues)


def test_verify_item_text_skips_citation_for_prose(tmp_path: Path) -> None:
    _, issues = verify_item_text(
        "decide on naming convention with team", tmp_path, require_citation=False
    )
    assert issues == []


# --- looks_like_code_work ---------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("implement Bar.parse", True),
        ("add a new harness adapter", True),
        ("refactor the bus module", True),
        ("decide on naming", False),
        ("write release notes paragraph", False),
    ],
)
def test_looks_like_code_work(text: str, expected: bool) -> None:
    assert looks_like_code_work(text) is expected


# --- verify_checklist (DB-backed) -------------------------------------------


def test_verify_checklist_only_done_by_default(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _write(tmp_path, "pkg/foo.py", "def bar():\n    return 1\n")
    _add_item(memdb, "t001", 0, "implement `pkg/foo.py:bar`", done=True)
    _add_item(memdb, "t001", 1, "implement `pkg/missing.py:nope`", done=False)

    result = verify_checklist(memdb, "t001", tmp_path)
    assert len(result.items) == 1
    assert result.overall_ok


def test_verify_checklist_flags_fake_done(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _write(tmp_path, "pkg/foo.py", "def bar():\n    pass\n")
    _add_item(memdb, "t001", 0, "implement `pkg/foo.py:bar`", done=True)

    result = verify_checklist(memdb, "t001", tmp_path)
    assert not result.overall_ok
    failing = result.failing()
    assert len(failing) == 1
    assert any("stub body" in i for i in failing[0].issues)


def test_verify_checklist_flags_codey_item_without_citation(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _add_item(memdb, "t001", 0, "implement the parser", done=True)

    result = verify_checklist(memdb, "t001", tmp_path)
    assert not result.overall_ok
    assert "no file citation" in result.items[0].issues[0]


def test_verify_checklist_passes_prose_item(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _add_item(memdb, "t001", 0, "decide on team naming convention", done=True)

    result = verify_checklist(memdb, "t001", tmp_path)
    assert result.overall_ok


def test_verify_checklist_dry_run_includes_undone(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _write(tmp_path, "pkg/foo.py", "def bar(): return 1\n")
    _add_item(memdb, "t001", 0, "implement `pkg/foo.py:bar`", done=False)

    result = verify_checklist(memdb, "t001", tmp_path, only_done=False)
    assert len(result.items) == 1
    assert result.items[0].done is False
    assert result.overall_ok


def test_format_report_includes_failing_items(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _write(tmp_path, "pkg/foo.py", "def bar():\n    pass\n")
    _add_item(memdb, "t001", 0, "implement `pkg/foo.py:bar`", done=True)

    result = verify_checklist(memdb, "t001", tmp_path)
    text = format_report(result)
    assert "FAIL" in text
    assert "stub body" in text


def test_format_report_all_ok(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_ticket(memdb)
    _write(tmp_path, "pkg/foo.py", "def bar():\n    return 1\n")
    _add_item(memdb, "t001", 0, "implement `pkg/foo.py:bar`", done=True)

    result = verify_checklist(memdb, "t001", tmp_path)
    text = format_report(result)
    assert "all 1 item(s) ok" in text
