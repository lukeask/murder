"""Seed and restore the copyable example artifacts under `.murder/`.

The canonical source lives in tracked package data (`murder.resources.templates`)
and is copied into the gitignored `.murder/` tree at runtime. The examples sit at
the `.murder/` top level — not under `tickets/` or `plans/` — so neither the
ticket nor the plan sync worker (which glob only their own subdirs) ingests them.
That keeps the defaults copyable but invisible to the TUI, and lets the service
restore either one if a user deletes it.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.paths import murder_dir

# (template filename in murder.resources.templates) -> destination filename under .murder/.
EXAMPLE_TEMPLATES: tuple[str, ...] = ("example_ticket.md", "example_plan.md")


def example_path(repo_root: Path, filename: str) -> Path:
    return murder_dir(repo_root) / filename


def _template_text(filename: str) -> str:
    return (
        resources.files("murder.resources.templates")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )


def seed_examples(repo_root: Path) -> list[Path]:
    """Restore any missing example artifact from the tracked templates.

    Idempotent: existing files are left untouched (a user may have edited a copy
    in place); only absent ones are (re-)created. Returns the paths written.
    """
    root = Path(repo_root)
    murder_dir(root).mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename in EXAMPLE_TEMPLATES:
        dest = example_path(root, filename)
        if dest.exists():
            continue
        atomic_write_text(dest, _template_text(filename))
        written.append(dest)
    return written


__all__ = ["EXAMPLE_TEMPLATES", "example_path", "seed_examples"]
