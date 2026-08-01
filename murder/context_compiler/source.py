"""Filesystem source reader bound to a worktree root.

Reads current source bytes from disk at assembly time. Does not consult
persisted index text or default to a control repository when a separate
worktree root is supplied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class SourceReadError(ValueError):
    """Raised when the reader cannot read or decode a source path safely."""


@dataclass(frozen=True, slots=True)
class FileSourceSnapshot:
    """Concrete source snapshot: decoded text, SHA-256 hex digest, line count."""

    text: str
    source_hash: str
    line_count: int


def count_source_lines(text: str) -> int:
    """Count one-based editor-style lines in decoded source text."""
    if text == "":
        return 0
    # splitlines() drops a final bare newline, matching typical line counts.
    return len(text.splitlines())


def hash_source_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of exact source bytes."""
    return hashlib.sha256(data).hexdigest()


def resolve_worktree_path(worktree_root: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``worktree_root`` with escape rejection.

    Rejects absolute paths, ``..`` components, and symlink escapes outside the
    worktree.
    """
    if relative_path == "":
        raise SourceReadError("path must be a non-empty relative path")
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise SourceReadError(f"reject absolute paths: {relative_path!r}")
    if ".." in candidate.parts:
        raise SourceReadError(f"reject path traversal: {relative_path!r}")

    root = worktree_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceReadError(f"path escapes worktree root {root}: {relative_path!r}") from exc
    return resolved


class FilesystemSourceReader:
    """``RepositorySourceReader`` implementation that reads from a worktree."""

    def __init__(self, worktree_root: Path) -> None:
        self._worktree_root = worktree_root

    @property
    def worktree_root(self) -> Path:
        return self._worktree_root

    def read(self, relative_path: str) -> FileSourceSnapshot:
        path = resolve_worktree_path(self._worktree_root, relative_path)
        if not path.is_file():
            raise SourceReadError(f"source path is not a file: {relative_path!r}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SourceReadError(f"failed to read {relative_path!r}: {exc}") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceReadError(f"failed to decode {relative_path!r} as UTF-8: {exc}") from exc
        return FileSourceSnapshot(
            text=text,
            source_hash=hash_source_bytes(raw),
            line_count=count_source_lines(text),
        )
