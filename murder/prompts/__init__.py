"""Prompt template loader.

Reads `<name>.md` from this package directory. Composition (e.g.
crow_<harness>.md + ticket-specific brief) happens at the call site.
"""

from __future__ import annotations

from importlib import resources


def load(name: str) -> str:
    """Return the text of `<name>.md` in this package, or raise FileNotFoundError."""
    fname = name if name.endswith(".md") else f"{name}.md"
    try:
        return resources.files("murder.prompts").joinpath(fname).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as e:
        raise FileNotFoundError(f"prompt template not found: {fname}") from e


def render(name: str, /, **fields: object) -> str:
    """Load a template and `.format(**fields)` it. Useful for crow prompts."""
    return load(name).format(**fields)
