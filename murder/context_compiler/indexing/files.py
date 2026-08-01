"""File enumeration, classification, and size policy for incremental indexing.

Classifies worktree paths as ``indexable``, ``text_only``, or ``ignored``.
Exclusion ideas borrow from ``murder.codebase_map.build`` (binary extensions,
lockfiles, fixture trees, vendor dirs) without coupling to that package or
assuming every accepted file must be LLM-summarizable.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from murder.context_compiler.extraction.registry import (
    EXTENSION_TO_LANGUAGE,
    ExtractorRegistry,
    default_registry,
)
from murder.context_compiler.persistence.files import normalize_relative_path

# ---------------------------------------------------------------------------
# Size policy (bytes). Separate ceilings — never one arbitrary limit for all.
# ---------------------------------------------------------------------------

# Structurally parse only when under this size.
DEFAULT_STRUCTURAL_PARSE_CEILING = 1_048_576  # 1 MiB

# Still hash + attach as text_only for lexical search up to this size.
DEFAULT_LEXICAL_SEARCH_CEILING = 2_097_152  # 2 MiB

# Do not read / index files larger than this.
DEFAULT_HARD_READ_CEILING = 10_485_760  # 10 MiB

# Git enumeration is an optimization; never let a stuck Git process block indexing.
GIT_ENUMERATION_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class SizePolicy:
    """Configurable byte ceilings for indexing decisions."""

    structural_parse_ceiling: int = DEFAULT_STRUCTURAL_PARSE_CEILING
    lexical_search_ceiling: int = DEFAULT_LEXICAL_SEARCH_CEILING
    hard_read_ceiling: int = DEFAULT_HARD_READ_CEILING

    def __post_init__(self) -> None:
        if not (
            0
            < self.structural_parse_ceiling
            <= self.lexical_search_ceiling
            <= self.hard_read_ceiling
        ):
            raise ValueError("size ceilings must satisfy 0 < structural <= lexical <= hard_read")


class FileClass(str, Enum):
    INDEXABLE = "indexable"
    TEXT_ONLY = "text_only"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class EnumeratedFile:
    """One worktree-relative path with classification and size metadata."""

    relative_path: str
    classification: FileClass
    byte_count: int
    language: str | None
    reason: str | None = None


# Binary / non-text extensions (aligned with codebase_map.build ideas).
_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svg",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".pyc",
        ".pyo",
        ".so",
        ".o",
        ".a",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".mov",
        ".avi",
        ".webm",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".jar",
        ".class",
        ".wasm",
        ".node",
    }
)

_GENERATED_NAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
    }
)

_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".jsonl")

_FIXTURE_DIRS = frozenset(
    {
        "fixtures",
        "__fixtures__",
        "snapshots",
        "__snapshots__",
        "testdata",
        "test_data",
        "cassettes",
    }
)

_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".murder",
        ".hg",
        ".svn",
        ".bzr",
        "node_modules",
        "vendor",
        "vendors",
        "third_party",
        "third-party",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".eggs",
        "dist",
        "build",
        "target",
        "out",
        "coverage",
        ".next",
        ".nuxt",
        ".turbo",
        ".cache",
        "Pods",
        "DerivedData",
    }
)


def _path_extension(relative_path: str) -> str:
    name = PurePosixPath(relative_path).name
    if name.startswith(".") and name.count(".") == 1:
        return ""
    return PurePosixPath(name).suffix.lower()


def _is_generated_or_fixture(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    low = relative_path.lower()
    if path.name.lower() in _GENERATED_NAMES:
        return True
    if low.endswith(_GENERATED_SUFFIXES) or low.endswith("-lock.json"):
        return True
    return bool(_FIXTURE_DIRS.intersection(part.lower() for part in path.parts))


def _has_ignored_dir(relative_path: str) -> bool:
    return bool(_IGNORED_DIR_NAMES.intersection(PurePosixPath(relative_path).parts))


def is_binary_extension(relative_path: str) -> bool:
    return _path_extension(relative_path) in _BINARY_EXTS


def looks_binary(data: bytes, *, sample_size: int = 8192) -> bool:
    """Heuristic: NUL bytes in the leading sample ⇒ binary."""
    sample = data[:sample_size]
    return b"\x00" in sample


def classify_path(
    relative_path: str,
    *,
    byte_count: int,
    size_policy: SizePolicy | None = None,
    registry: ExtractorRegistry | None = None,
    language_hint: str | None = None,
) -> EnumeratedFile:
    """Classify a normalized relative path without reading file contents."""
    policy = size_policy or SizePolicy()
    reg = registry if registry is not None else default_registry()

    try:
        path = normalize_relative_path(relative_path)
    except ValueError as exc:
        return EnumeratedFile(
            relative_path=relative_path,
            classification=FileClass.IGNORED,
            byte_count=max(0, byte_count),
            language=None,
            reason=str(exc),
        )

    if _has_ignored_dir(path):
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.IGNORED,
            byte_count=byte_count,
            language=None,
            reason="ignored directory",
        )
    if is_binary_extension(path):
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.IGNORED,
            byte_count=byte_count,
            language=None,
            reason="binary extension",
        )
    if _is_generated_or_fixture(path):
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.IGNORED,
            byte_count=byte_count,
            language=None,
            reason="generated or fixture",
        )
    if byte_count > policy.hard_read_ceiling:
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.IGNORED,
            byte_count=byte_count,
            language=None,
            reason="exceeds hard read ceiling",
        )

    language = reg.resolve_language(path, language_hint)
    if language is None:
        ext = _path_extension(path)
        language = EXTENSION_TO_LANGUAGE.get(ext)

    pipeline = reg.select(path, language_hint=language_hint)
    under_structural = byte_count <= policy.structural_parse_ceiling
    under_lexical = byte_count <= policy.lexical_search_ceiling

    if pipeline is not None and under_structural:
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.INDEXABLE,
            byte_count=byte_count,
            language=language or pipeline.language or None,
            reason=None,
        )

    if under_lexical:
        reason = (
            "no structural extractor" if pipeline is None else "exceeds structural parse ceiling"
        )
        return EnumeratedFile(
            relative_path=path,
            classification=FileClass.TEXT_ONLY,
            byte_count=byte_count,
            language=language,
            reason=reason,
        )

    return EnumeratedFile(
        relative_path=path,
        classification=FileClass.IGNORED,
        byte_count=byte_count,
        language=language,
        reason="exceeds lexical search ceiling",
    )


async def _git_list_files(worktree_root: Path) -> list[str] | None:
    """Tracked + untracked (non-ignored) paths via Git, or ``None`` if unavailable."""
    if not (worktree_root / ".git").exists():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(worktree_root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        return None
    try:
        stdout_raw, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=GIT_ENUMERATION_TIMEOUT_SECONDS
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    out = stdout_raw.decode("utf-8", errors="replace")
    return [line for line in out.split("\0") if line]


def _walk_filesystem(worktree_root: Path) -> list[str]:
    """Fallback enumeration when Git is unavailable."""
    root = worktree_root.resolve()
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place.
        kept: list[str] = []
        for name in dirnames:
            if name in _IGNORED_DIR_NAMES:
                continue
            kept.append(name)
        dirnames[:] = kept
        rel_dir = Path(dirpath).resolve().relative_to(root)
        for name in filenames:
            if name in _IGNORED_DIR_NAMES:
                continue
            rel = name if str(rel_dir) == "." else f"{rel_dir.as_posix()}/{name}"
            results.append(rel.replace("\\", "/"))
    results.sort()
    return results


def _stat_byte_count(worktree_root: Path, relative_path: str) -> int | None:
    try:
        return (worktree_root / relative_path).stat().st_size
    except OSError:
        return None


async def enumerate_worktree_files(
    worktree_root: Path,
    *,
    size_policy: SizePolicy | None = None,
    registry: ExtractorRegistry | None = None,
) -> list[EnumeratedFile]:
    """List and classify files under ``worktree_root``.

    Prefers ``git ls-files`` (cached + others, exclude-standard). Falls back to
    a pruned filesystem walk. Returns entries sorted by relative path.
    """
    policy = size_policy or SizePolicy()
    reg = registry if registry is not None else default_registry()
    root = worktree_root.resolve()

    paths = await _git_list_files(root)
    if paths is None:
        paths = _walk_filesystem(root)
    else:
        paths = sorted(set(paths))

    results: list[EnumeratedFile] = []
    for raw in paths:
        try:
            normalized = normalize_relative_path(raw)
        except ValueError:
            continue
        size = _stat_byte_count(root, normalized)
        if size is None:
            continue
        results.append(
            classify_path(
                normalized,
                byte_count=size,
                size_policy=policy,
                registry=reg,
            )
        )
    results.sort(key=lambda item: item.relative_path)
    return results


__all__ = [
    "DEFAULT_HARD_READ_CEILING",
    "DEFAULT_LEXICAL_SEARCH_CEILING",
    "DEFAULT_STRUCTURAL_PARSE_CEILING",
    "EnumeratedFile",
    "FileClass",
    "SizePolicy",
    "classify_path",
    "enumerate_worktree_files",
    "is_binary_extension",
    "looks_binary",
]
