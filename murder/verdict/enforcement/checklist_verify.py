"""Anti-faking checklist verification.

Checklist state is synced from ticket markdown. A checked item still does not
prove the work landed, so this module inspects each item's text for cited
file/symbol references and verifies
against the working tree:

- Cited path exists.
- If a symbol is named (function/class/method), it exists in the file.
- The symbol's body isn't a stub (`pass`, `...`, bare `raise
  NotImplementedError`, lone docstring, empty).

Heuristic, not airtight: it can't catch semantically-wrong code, only
empty claims. But it raises the floor — "I checked off `parse Foo` but
the function is still `raise NotImplementedError`" stops being silent.

Pure module: callers feed in a sqlite connection + repo_root + ticket_id
and get back a structured `VerificationResult`. Wiring into the
post-crow-done flow is a separate decision.
"""

from __future__ import annotations

import ast
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# Backtick-wrapped tokens that look like file references.
# Accepts:
#   `path/to/file.py`
#   `path/to/file.py:Symbol`
#   `path/to/file.py:Symbol.method`
#   `path/to/file.py::Symbol`        (pytest-style)
#   `path/to/file.py#Symbol`         (anchor-style)
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# A "looks-like-file" token: contains a path separator or a recognised
# code suffix. Avoids matching plain prose backtick-quoted words.
_FILE_LIKE_RE = re.compile(
    r"""
    ^
    (?P<path>
        [\w./\-]+?
        (?:\.(?:py|md|toml|yaml|yml|json|sql|sh|js|ts|tsx|html|css|txt))
    )
    (?:
        (?:[:#]|::)
        (?P<symbol>[\w.]+)
    )?
    $
    """,
    re.VERBOSE,
)

# Bare-dotted-name reference fallback (e.g., `murder.bus.Bus.publish`).
# Resolves only if the dotted prefix maps to a real file under repo_root.
_DOTTED_RE = re.compile(r"^[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+$")


@dataclass(frozen=True)
class CitedReference:
    raw: str
    path: Path
    symbol: str | None = None


@dataclass
class ItemFinding:
    item_id: int
    ord: int
    text: str
    done: bool
    citations: list[CitedReference] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class VerificationResult:
    ticket_id: str
    items: list[ItemFinding]

    @property
    def overall_ok(self) -> bool:
        return all(item.ok for item in self.items)

    def failing(self) -> list[ItemFinding]:
        return [i for i in self.items if not i.ok]


# --- Citation extraction ----------------------------------------------------


def extract_citations(text: str, repo_root: Path) -> list[CitedReference]:
    """Pull plausible file/symbol references out of free-form item text.

    Recognises backtick-quoted tokens that look like paths or
    file:symbol pairs, plus bare dotted names that resolve to a `.py`
    file on disk.
    """
    seen: set[tuple[str, str | None]] = set()
    out: list[CitedReference] = []

    for token in _BACKTICK_RE.findall(text):
        ref = _parse_token(token, repo_root)
        if ref is None:
            continue
        key = (str(ref.path), ref.symbol)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)

    # Bare dotted names outside backticks (looser; only emit if file exists).
    outside = _BACKTICK_RE.sub(" ", text)
    for word in re.findall(r"\b[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+\b", outside):
        if not _DOTTED_RE.match(word):
            continue
        ref = _resolve_dotted(word, repo_root)
        if ref is None:
            continue
        key = (str(ref.path), ref.symbol)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)

    return out


def _parse_token(token: str, repo_root: Path) -> CitedReference | None:
    token = token.strip()
    if not token:
        return None
    m = _FILE_LIKE_RE.match(token)
    if m is None:
        return None
    path = Path(m.group("path"))
    if path.is_absolute():
        return None
    return CitedReference(raw=token, path=path, symbol=m.group("symbol"))


def _resolve_dotted(dotted: str, repo_root: Path) -> CitedReference | None:
    """Try `a.b.c` -> `a/b/c.py`, then `a/b.py` symbol `c`, then `a/b/c/__init__.py`."""
    parts = dotted.split(".")
    # Longest-prefix-as-path first.
    for split in range(len(parts), 0, -1):
        path_parts = parts[:split]
        symbol_parts = parts[split:]
        candidate = repo_root.joinpath(*path_parts).with_suffix(".py")
        if candidate.is_file():
            return CitedReference(
                raw=dotted,
                path=Path(*path_parts).with_suffix(".py"),
                symbol=".".join(symbol_parts) if symbol_parts else None,
            )
        pkg = repo_root.joinpath(*path_parts, "__init__.py")
        if pkg.is_file():
            return CitedReference(
                raw=dotted,
                path=Path(*path_parts, "__init__.py"),
                symbol=".".join(symbol_parts) if symbol_parts else None,
            )
    return None


# --- Stub detection ---------------------------------------------------------


def is_stub_file(abs_path: Path) -> tuple[bool, str]:
    """Whole file looks empty / placeholder."""
    try:
        src = abs_path.read_text(encoding="utf-8")
    except OSError as e:
        return True, f"unreadable: {e}"
    stripped = src.strip()
    if not stripped:
        return True, "file is empty"
    try:
        tree = ast.parse(src, filename=str(abs_path))
    except SyntaxError as e:
        return True, f"syntax error: {e.msg}"
    if not tree.body:
        return True, "module body is empty"
    if all(_is_stub_node(node) for node in tree.body):
        return True, "module body contains only stubs / docstrings"
    return False, ""


def is_stub_symbol(abs_path: Path, symbol: str) -> tuple[bool, str]:  # noqa: PLR0911
    """Symbol exists and has a non-stub body. Symbol may be dotted (Class.method)."""
    try:
        src = abs_path.read_text(encoding="utf-8")
    except OSError as e:
        return True, f"unreadable: {e}"
    try:
        tree = ast.parse(src, filename=str(abs_path))
    except SyntaxError as e:
        return True, f"syntax error: {e.msg}"

    parts = symbol.split(".")
    node = _find_symbol(tree, parts)
    if node is None:
        return True, f"symbol `{symbol}` not found"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if _func_body_is_stub(node):
            return True, f"`{symbol}` is a stub body"
        return False, ""
    if isinstance(node, ast.ClassDef):
        # Class is "real" if any non-stub method or any non-trivial assignment.
        if _class_body_is_stub(node):
            return True, f"class `{symbol}` has only stub members"
        return False, ""
    # Variable assignment etc. — accept presence as enough.
    return False, ""


def _find_symbol(
    tree: ast.AST, parts: list[str]
) -> ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Assign | None:
    if not parts:
        return None
    head, rest = parts[0], parts[1:]
    for node in ast.iter_child_nodes(tree):
        name = getattr(node, "name", None)
        if name == head:
            if not rest:
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                ):
                    return node
                return None
            return _find_symbol(node, rest)
        # Module-level variable assignments.
        if not rest and isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == head:
                    return node
    return None


def _is_docstring_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_stub_statement(node: ast.stmt) -> bool:
    if isinstance(node, ast.Pass):
        return True
    if _is_docstring_expr(node):
        return True
    # `...` ellipsis as a bare expression
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    ):
        return True
    if isinstance(node, ast.Raise):
        exc = node.exc
        # `raise NotImplementedError` or `raise NotImplementedError("...")`
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
        if (
            isinstance(exc, ast.Call)
            and isinstance(exc.func, ast.Name)
            and exc.func.id == "NotImplementedError"
        ):
            return True
    return False


def _func_body_is_stub(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(func.body)
    if not body:
        return True
    return all(_is_stub_statement(stmt) for stmt in body)


def _is_stub_node(node: ast.stmt) -> bool:
    """Module-level statement that contributes nothing real."""
    if _is_stub_statement(node):
        return True
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _func_body_is_stub(node)
    if isinstance(node, ast.ClassDef):
        return _class_body_is_stub(node)
    return False


def _class_body_is_stub(cls: ast.ClassDef) -> bool:
    if not cls.body:
        return True
    real = 0
    for node in cls.body:
        if _is_docstring_expr(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _func_body_is_stub(node):
                continue
            real += 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            real += 1
        elif _is_stub_statement(node):
            continue
        else:
            real += 1
    return real == 0


# --- Per-item verification --------------------------------------------------


def verify_item_text(
    text: str,
    repo_root: Path,
    *,
    require_citation: bool,
) -> tuple[list[CitedReference], list[str]]:
    """Inspect one checklist item; return (citations, issues)."""
    citations = extract_citations(text, repo_root)
    issues: list[str] = []

    if not citations:
        if require_citation:
            issues.append(
                "no file citation found; expected `path/to/file.py` "
                "or `path/to/file.py:Symbol` in the item text"
            )
        return citations, issues

    for ref in citations:
        abs_path = (repo_root / ref.path).resolve()
        try:
            abs_path.relative_to(repo_root.resolve())
        except ValueError:
            issues.append(f"cited path escapes repo root: {ref.path}")
            continue
        if not abs_path.is_file():
            issues.append(f"cited path does not exist: {ref.path}")
            continue
        if ref.symbol is None:
            stub, reason = is_stub_file(abs_path)
            if stub:
                issues.append(f"{ref.path}: {reason}")
        else:
            stub, reason = is_stub_symbol(abs_path, ref.symbol)
            if stub:
                issues.append(f"{ref.path}: {reason}")

    return citations, issues


# Heuristic: items whose text reads like prose-only chores (e.g., "write
# a paragraph in the README", "decide on naming") shouldn't be required
# to cite a code symbol. We default to requiring citations only for
# items that look like they describe code work.
_CODEY_HINTS = (
    "implement",
    "add",
    "wire",
    "extend",
    "refactor",
    "fix",
    "remove",
    "rename",
    "split",
    "extract",
    "introduce",
    "delete",
    "patch",
    "register",
    "expose",
    "hook",
    "instrument",
    "thread",
    "handler",
    "function",
    "class",
    "method",
    "module",
)


def looks_like_code_work(text: str) -> bool:
    lower = text.lower()
    return any(hint in lower for hint in _CODEY_HINTS)


def verify_checklist(
    conn: sqlite3.Connection,
    ticket_id: str,
    repo_root: Path,
    *,
    only_done: bool = True,
    require_citation_when_codey: bool = True,
) -> VerificationResult:
    """Verify all checklist items for `ticket_id` against the working tree.

    By default only checks items already marked `done = 1`; the goal is
    to catch fakes after the fact. Pass `only_done=False` to dry-run
    against in-progress items.

    `require_citation_when_codey` toggles the heuristic that flags
    code-shaped items missing any backtick file reference. Prose items
    (e.g. "decide on naming") are always exempt.
    """
    rows = conn.execute(
        "SELECT id, ord, text, done FROM checklist WHERE ticket_id = ? ORDER BY ord",
        (ticket_id,),
    ).fetchall()

    items: list[ItemFinding] = []
    for row in rows:
        done = bool(row["done"])
        if only_done and not done:
            continue
        require = require_citation_when_codey and looks_like_code_work(row["text"])
        citations, issues = verify_item_text(row["text"], repo_root, require_citation=require)
        items.append(
            ItemFinding(
                item_id=int(row["id"]),
                ord=int(row["ord"]),
                text=row["text"],
                done=done,
                citations=list(citations),
                issues=issues,
            )
        )

    return VerificationResult(ticket_id=ticket_id, items=items)


def format_report(result: VerificationResult) -> str:
    """Human-readable summary; safe for tmux pane / escalation body."""
    lines = [f"checklist verification — ticket {result.ticket_id}"]
    if result.overall_ok:
        lines.append(f"  all {len(result.items)} item(s) ok")
        return "\n".join(lines)
    for item in result.items:
        marker = "ok" if item.ok else "FAIL"
        lines.append(f"  [{marker}] #{item.ord}: {item.text}")
        for issue in item.issues:
            lines.append(f"      - {issue}")
    return "\n".join(lines)
