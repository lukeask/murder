"""Shared parameterised sync loop for notes and reports.

``SimpleDocSync`` is a thin concrete subclass of ``MarkdownSyncLoop`` that
handles markdown-only artifacts: no frontmatter, no parser, plain body text.
It is parameterised at construction time with:

- ``dir_fn`` — ``(repo_root) -> Path`` for the artifact directory
- ``md_path_fn`` — ``(repo_root, name) -> Path`` for a single artifact file
- ``list_fn`` — ``(db) -> list[dict]`` — list active rows (returns size, not body)
- ``get_fn`` — ``(db, name) -> dict | None``
- ``upsert_fn`` — ``(db, name, *, body, materialized_path) -> None``
- ``insert_revision_fn`` — ``(db, name, *, source, body, content_hash) -> int``
- ``entity`` — ``Entity`` for the ``notify_changed`` seam (F5.1)

Notes and reports are structural twins (no frontmatter, body-only); both use
this class.  ``NotetakerContextSync`` and ``PlanSync`` remain separate
subclasses — they are genuinely different (singleton, parser, etc.).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.markdown_loop import MarkdownSyncLoop

if TYPE_CHECKING:  # avoid runtime bus.protocol import from state.storage
    from murder.bus.protocol import Entity


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SimpleDocSync(MarkdownSyncLoop):
    """Shared reconcile loop for plain-markdown artifacts (notes, reports).

    One reconcile algorithm backs both artifact types; callers supply the
    DAO callables and directory helpers as constructor arguments.
    """

    def __init__(
        self,
        repo_root: Path,
        db: Any,
        *,
        dir_fn: Callable[[Path], Path],
        md_path_fn: Callable[[Path, str], Path],
        list_fn: Callable[[Any], list[dict[str, Any]]],
        get_fn: Callable[[Any, str], dict[str, Any] | None],
        upsert_fn: Callable[..., None],
        insert_revision_fn: Callable[..., int],
        entity: "Entity",
        poll_s: float = 1.5,
        debounce_s: float = 0.75,
        on_change: "Callable[[Entity, str], Any] | None" = None,
    ) -> None:
        super().__init__(
            repo_root,
            poll_s=poll_s,
            debounce_s=debounce_s,
            entity=entity,
            on_change=on_change,
        )
        self.db = db
        self._dir_fn = dir_fn
        self._md_path_fn = md_path_fn
        self._list_fn = list_fn
        self._get_fn = get_fn
        self._upsert_fn = upsert_fn
        self._insert_revision_fn = insert_revision_fn

    async def reconcile_all(self) -> None:
        self._dir_fn(self.repo_root).mkdir(parents=True, exist_ok=True)
        for row in self._list_fn(self.db):
            path = self._md_path_fn(self.repo_root, str(row["name"]))
            if not path.exists():
                full = self._get_fn(self.db, str(row["name"]))
                if full is not None:
                    atomic_write_text(path, str(full["body"]))
        for path in self.scan_paths():
            await self.reconcile_file(path)

    async def reconcile_file(self, path: Path) -> None:
        name = path.stem
        rel = str(path.relative_to(self.repo_root))
        body = path.read_text(encoding="utf-8")
        row = self._get_fn(self.db, name)
        if row is None:
            self._upsert_fn(self.db, name, body=body, materialized_path=rel)
            self._insert_revision_fn(
                self.db,
                name,
                source="file_import",
                body=body,
                content_hash=_content_hash(body),
            )
            # New file import — a doc appeared in the active list. Emit once via
            # the async notify_changed seam (F5.1). This is the ONLY emit path for
            # SimpleDocSync; the old on_note_change sync callback is gone.
            await self.notify_changed(name)
            return
        if str(row["body"]) != body or str(row["materialized_path"]) != rel:
            self._upsert_fn(self.db, name, body=body, materialized_path=rel)
            if str(row["body"]) != body:
                self._insert_revision_fn(
                    self.db,
                    name,
                    source="file_import",
                    body=body,
                    content_hash=_content_hash(body),
                )
            # Existing doc's body/path changed. Emit once.
            await self.notify_changed(name)

    def scan_paths(self) -> list[Path]:
        root = self._dir_fn(self.repo_root)
        if not root.exists():
            return []
        return sorted(p for p in root.glob("*.md") if p.is_file())
