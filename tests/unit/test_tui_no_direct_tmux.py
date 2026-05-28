"""murder/tui must not call tmux directly — bus/RPC only."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_TUI_ROOT = Path(__file__).resolve().parents[2] / "murder" / "tui"

_FAILURE_PREAMBLE = (
    "murder/tui must not use direct tmux access. "
    "Use TuiRuntimeClient bus methods (capture_pane, shell_run, agent.send_key, …)."
)

_FORBIDDEN_MODULE_PREFIXES: tuple[str, ...] = (
    "murder.terminal.tmux",
)

_FORBIDDEN_IMPORTED_NAMES: frozenset[str] = frozenset({"tmux"})

_FORBIDDEN_ATTR_CALL_ROOTS: frozenset[str] = frozenset({"tmux"})

_FORBIDDEN_TMUX_ATTRS: frozenset[str] = frozenset(
    {
        "capture_pane",
        "send_keys",
        "create_session",
        "kill_session",
        "list_sessions",
        "session_exists",
    }
)


@dataclass(frozen=True, slots=True)
class _Violation:
    path: Path
    lineno: int
    detail: str

    def format(self) -> str:
        rel = self.path.relative_to(_TUI_ROOT.parent.parent)
        return f"  {rel}:{self.lineno}: {self.detail}"


class _TuiTmuxGuard(ast.NodeVisitor):
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
            if alias.asname in _FORBIDDEN_IMPORTED_NAMES or alias.name in _FORBIDDEN_IMPORTED_NAMES:
                self._add(node, f"forbidden import name {alias.name!r}")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        hit = self._module_forbidden(module)
        if hit is not None:
            self._add(node, f"forbidden import from {module!r} (via {hit})")
        if module == "murder.terminal":
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORTED_NAMES:
                    self._add(node, f"forbidden import of tmux from murder.terminal")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            if (
                func.attr in _FORBIDDEN_TMUX_ATTRS
                and isinstance(func.value, ast.Name)
                and func.value.id in _FORBIDDEN_ATTR_CALL_ROOTS
            ):
                self._add(node, f"forbidden tmux.{func.attr}() call")
        self.generic_visit(node)


def _collect_violations() -> list[_Violation]:
    violations: list[_Violation] = []
    for path in sorted(_TUI_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        guard = _TuiTmuxGuard(path)
        guard.visit(tree)
        violations.extend(guard.violations)
    return violations


def _violations_in_source(source: str, *, path: Path | None = None) -> list[_Violation]:
    tree = ast.parse(source)
    guard = _TuiTmuxGuard(path or Path("example.py"))
    guard.visit(tree)
    return guard.violations


def test_tmux_guard_detects_forbidden_patterns() -> None:
    samples = (
        "from murder.terminal import tmux\n",
        "import murder.terminal.tmux as tmux\n",
        "tmux.capture_pane('s', lines=10)\n",
        "await tmux.send_keys('s', 'Enter', literal=False)\n",
    )
    for sample in samples:
        assert _violations_in_source(sample), f"expected violation for: {sample!r}"


def test_tui_package_has_no_direct_tmux() -> None:
    violations = _collect_violations()
    if not violations:
        return
    lines = [_FAILURE_PREAMBLE, "Violations:"]
    lines.extend(v.format() for v in violations)
    pytest.fail("\n".join(lines))
