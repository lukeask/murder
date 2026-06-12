"""Stdlib-``ast`` symbol extractor for ``.py`` files.

Walks the module-level body plus one level of class bodies — flat and
cheap. Nested functions, comprehensions, and anything below class-method
level are deliberately not enumerated.
"""

from __future__ import annotations

import ast

from murder.codebase_map.extract.base import Symbol


class PythonAstExtractor:
    """Enumerate module-level functions, classes (+ methods), and constants."""

    def extract(self, path: str, src: str) -> list[Symbol]:
        try:
            tree = ast.parse(src)
        except SyntaxError:
            # We have an extractor for .py — the file just didn't parse.
            # Return [] (not None); the LLM can still read the raw source.
            return []

        symbols: list[Symbol] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(_function_symbol(node, kind="function"))
            elif isinstance(node, ast.ClassDef):
                symbols.extend(_class_symbols(node))
            elif isinstance(node, ast.Assign):
                symbols.extend(_assign_constants(node))
            elif isinstance(node, ast.AnnAssign):
                sym = _annassign_constant(node)
                if sym is not None:
                    symbols.append(sym)

        symbols.sort(key=lambda s: s.lineno)
        return symbols


def _function_symbol(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    kind: str,
    qualifier: str = "",
) -> Symbol:
    name = f"{qualifier}.{node.name}" if qualifier else node.name
    return Symbol(
        kind=kind,
        name=name,
        signature=_function_signature(node),
        lineno=node.lineno,
        docstring=_first_doc_line(node),
    )


def _class_symbols(node: ast.ClassDef) -> list[Symbol]:
    symbols = [
        Symbol(
            kind="class",
            name=node.name,
            signature=_class_signature(node),
            lineno=node.lineno,
            docstring=_first_doc_line(node),
        )
    ]
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_function_symbol(child, kind="method", qualifier=node.name))
    return symbols


def _assign_constants(node: ast.Assign) -> list[Symbol]:
    out: list[Symbol] = []
    for target in node.targets:
        if isinstance(target, ast.Name) and _is_constant_name(target.id):
            out.append(
                Symbol(
                    kind="constant",
                    name=target.id,
                    signature=target.id,
                    lineno=node.lineno,
                    docstring=None,
                )
            )
    return out


def _annassign_constant(node: ast.AnnAssign) -> Symbol | None:
    if not isinstance(node.target, ast.Name):
        return None
    name = node.target.id
    # Annotated module-level names count as constants; skip `_`-prefixed ones.
    if name.startswith("_"):
        return None
    annotation = _unparse(node.annotation)
    signature = f"{name}: {annotation}" if annotation else name
    return Symbol(
        kind="constant",
        name=name,
        signature=signature,
        lineno=node.lineno,
        docstring=None,
    )


def _is_constant_name(name: str) -> bool:
    if name.startswith("_"):
        return False
    return name.isupper() and name == name.upper() and any(c.isalpha() for c in name)


def _class_signature(node: ast.ClassDef) -> str:
    bases = [_unparse(base) for base in node.bases]
    bases += [f"{kw.arg}={_unparse(kw.value)}" for kw in node.keywords if kw.arg]
    if bases:
        return f"class {node.name}({', '.join(bases)})"
    return f"class {node.name}"


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    params = _render_params(node.args)
    sig = f"{prefix} {node.name}({params})"
    if node.returns is not None:
        ret = _unparse(node.returns)
        if ret:
            sig += f" -> {ret}"
    return sig


def _render_params(args: ast.arguments) -> str:
    parts: list[str] = []

    posonly = list(args.posonlyargs)
    regular = list(args.args)
    # Defaults align to the tail of posonly + regular.
    positional = posonly + regular
    defaults = list(args.defaults)
    default_offset = len(positional) - len(defaults)

    for idx, arg in enumerate(positional):
        has_default = idx >= default_offset
        parts.append(_render_arg(arg, has_default=has_default))
        if posonly and idx == len(posonly) - 1:
            parts.append("/")

    if args.vararg is not None:
        parts.append("*" + _render_arg(args.vararg, has_default=False))
    elif args.kwonlyargs:
        parts.append("*")

    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_render_arg(arg, has_default=default is not None))

    if args.kwarg is not None:
        parts.append("**" + _render_arg(args.kwarg, has_default=False))

    return ", ".join(parts)


def _render_arg(arg: ast.arg, *, has_default: bool) -> str:
    rendered = arg.arg
    if arg.annotation is not None:
        annotation = _unparse(arg.annotation)
        if annotation:
            rendered += f": {annotation}"
    if has_default:
        # Do not source-render default values; presence only.
        rendered += "=..." if arg.annotation is None else " = ..."
    return rendered


def _first_doc_line(node: ast.AST) -> str | None:
    doc = ast.get_docstring(node)
    if not doc:
        return None
    first = doc.strip().splitlines()
    if not first:
        return None
    return first[0].strip() or None


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive; ast.unparse is stdlib
        return ""
