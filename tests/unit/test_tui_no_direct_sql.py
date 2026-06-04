"""murder/tui must not touch SQLite or persistence — bus/RPC only."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_TUI_ROOT = Path(__file__).resolve().parents[2] / "murder" / "tui"

_FAILURE_PREAMBLE = (
    "murder/tui must not use direct SQLite or persistence access. "
    "All communication must happen through the bus."
)

# Module paths (exact or prefix) that must not be imported from the TUI package.
_FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "sqlite3",
    "murder.state.persistence",
    "murder.app.service.read_model",
    "murder.verdict.escalations.views",
    "murder.db",
)

# Symbol names that must not be imported into murder/tui (from any module).
_FORBIDDEN_IMPORTED_NAMES: frozenset[str] = frozenset(
    {
        "get_db",
        "init_db",
        "ServiceReadModel",
        "ack_escalation_db",
    }
)

# Callables that must not be invoked from murder/tui.
_FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset({"get_db", "connect"})


@dataclass(frozen=True, slots=True)
class _Violation:
    path: Path
    lineno: int
    detail: str

    def format(self) -> str:
        rel = self.path.relative_to(_TUI_ROOT.parent.parent)
        return f"  {rel}:{self.lineno}: {self.detail}"


class _TuiSqlGuard(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.violations: list[_Violation] = []

    def _add(self, node: ast.AST, detail: str) -> None:
        self.violations.append(_Violation(self.path, node.lineno, detail))

    def _module_forbidden(self, module: str) -> str | None:
        for prefix in _FORBIDDEN_MODULE_PREFIXES:
            if module == prefix or module.startswith(f"{prefix}."):
                return prefix
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            hit = self._module_forbidden(alias.name)
            if hit is not None:
                self._add(node, f"forbidden import {alias.name!r} (via {hit})")
            if alias.name in _FORBIDDEN_IMPORTED_NAMES:
                self._add(node, f"forbidden import name {alias.name!r}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        hit = self._module_forbidden(module)
        if hit is not None:
            self._add(node, f"forbidden import from {module!r} (via {hit})")
        for alias in node.names:
            name = alias.name
            if name in _FORBIDDEN_IMPORTED_NAMES:
                self._add(node, f"forbidden import of {name!r} from {module!r}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "read_model":
            self._add(
                node,
                "forbidden attribute read_model (use MurderServiceClient bus methods)",
            )
        if node.attr == "execute" and isinstance(node.value, ast.Name):
            if node.value.id in {"db", "conn", "cursor"}:
                self._add(node, f"forbidden {node.value.id}.execute() SQL call")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALL_NAMES:
            self._add(node, f"forbidden call to {func.id}()")
        if isinstance(func, ast.Attribute):
            if func.attr == "execute" and isinstance(func.value, ast.Name):
                if func.value.id in {"db", "conn", "cursor"}:
                    self._add(node, f"forbidden {func.value.id}.execute() SQL call")
        self.generic_visit(node)


def _collect_violations() -> list[_Violation]:
    violations: list[_Violation] = []
    for path in sorted(_TUI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        guard = _TuiSqlGuard(path)
        guard.visit(tree)
        violations.extend(guard.violations)
    return violations


def _violations_in_source(source: str, *, path: Path | None = None) -> list[_Violation]:
    tree = ast.parse(source)
    guard = _TuiSqlGuard(path or Path("example.py"))
    guard.visit(tree)
    return guard.violations


def test_sql_guard_detects_forbidden_patterns() -> None:
    """Cookbook: guard must flag representative direct-SQL / read_model patterns."""
    samples = (
        "import sqlite3\n",
        "from murder.state.persistence.schema import get_db\n",
        "from murder.app.service.read_model import ServiceReadModel\n",
        "runtime.read_model.get_dispatch_snapshot()\n",
        "db.execute('SELECT 1')\n",
    )
    for sample in samples:
        assert _violations_in_source(sample), f"expected violation for: {sample!r}"


def test_tui_package_has_no_direct_sql_or_persistence() -> None:
    violations = _collect_violations()
    if not violations:
        return
    lines = [_FAILURE_PREAMBLE, "Violations:"]
    lines.extend(v.format() for v in violations)
    pytest.fail("\n".join(lines))
