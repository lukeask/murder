"""On-disk renderer for the codebase map (t059).

Writes the ``.murder/map/`` mirror tree: one ``<relpath>.md`` per source file
(keeping the source extension so ``foo.py.md`` and ``foo.pyi.md`` don't
collide), plus a ``DIR.md`` per directory and a ``ROOT.md`` at the top.

Every rendered file carries YAML frontmatter so the map self-reports
staleness. Writes are atomic (tmp + ``os.replace``) and parent dirs are
created on demand.

This module renders the live working copy only. DB persistence is t060.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from murder.codebase_map.summarize import FileSummary
from murder.codebase_map.tokens import count_tokens


def map_root_for(repo_root: Path) -> Path:
    """The map tree root: ``<repo_root>/.murder/map``."""
    return repo_root / ".murder" / "map"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _frontmatter(fields: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def render_file_summary(map_root: Path, repo_rel: str, summary: FileSummary) -> None:
    """Render a per-file summary to ``<map_root>/<repo_rel>.md``."""
    target = map_root / (repo_rel + ".md")
    front = _frontmatter(
        {
            "source_path": repo_rel,
            "source_hash": summary.source_hash,
            "source_tokens": summary.source_tokens,
            "summary_tokens": summary.summary_tokens,
            "generated_at": _now(),
        }
    )
    _atomic_write(target, f"{front}\n\n{summary.body.strip()}\n")


def render_dir_summary(map_root: Path, repo_rel_dir: str, body: str) -> None:
    """Render a directory roll-up to ``<map_root>/<repo_rel_dir>/DIR.md``."""
    target = map_root / repo_rel_dir / "DIR.md" if repo_rel_dir else map_root / "DIR.md"
    front = _frontmatter(
        {
            "source_path": repo_rel_dir or ".",
            "source_hash": None,
            "source_tokens": None,
            "summary_tokens": count_tokens(body),
            "generated_at": _now(),
        }
    )
    _atomic_write(target, f"{front}\n\n{body.strip()}\n")


def render_root(map_root: Path, body: str) -> None:
    """Render the top-level roll-up to ``<map_root>/ROOT.md``."""
    target = map_root / "ROOT.md"
    front = _frontmatter(
        {
            "source_path": ".",
            "source_hash": None,
            "source_tokens": None,
            "summary_tokens": count_tokens(body),
            "generated_at": _now(),
        }
    )
    _atomic_write(target, f"{front}\n\n{body.strip()}\n")


__all__ = [
    "map_root_for",
    "render_file_summary",
    "render_dir_summary",
    "render_root",
]
