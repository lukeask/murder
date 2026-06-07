"""Document stores for plans, notes, and reports.

Three closely-parallel store types, each holding a list snapshot plus
lazily-loaded body content for the selected document. Extracted from the
poll-fed paths in app.py (_refresh_bus_views) and the ad-hoc render helpers
(_render_plan / _render_note / _render_report).

Change detection intentionally excludes `as_of` (which changes every poll);
only `invalidation_key` and `items` tuple equality drive notify — same as
the widget's `new_rows` diffing today.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from murder.app.service.client_api import (
    NoteDisplaySnapshot,
    NotesSnapshot,
    NoteSummary,
    PlanDisplaySnapshot,
    PlansSnapshot,
    PlanSummary,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ReportSummary,
)
from murder.app.tui.stores.base import BaseStore

ItemT = TypeVar("ItemT")
ListSnapT = TypeVar("ListSnapT")
DisplaySnapT = TypeVar("DisplaySnapT")


@dataclass(frozen=True, slots=True)
class DocumentStoreSnapshot(Generic[ItemT]):
    """Immutable snapshot emitted by any document store.

    ``bodies`` is a sorted (name, markdown) tuple so equality is stable.
    ``as_of`` from the server snapshot is deliberately excluded — it advances
    on every poll even when content is unchanged.

    ``rows`` holds pre-derived display rows ready for the list widget to render
    without any further derivation.  The tuple type varies by store subclass
    (plans: (display_name, status, revision, sync_state); notes/reports:
    (name, char_count, updated_at[:16])).  Cursor/selection remain view-local.
    """

    items: tuple[ItemT, ...]
    invalidation_key: str
    selected_name: str | None
    bodies: tuple[tuple[str, str], ...]  # sorted (name, markdown)
    rows: tuple[tuple[str, ...], ...] = ()  # pre-derived display rows


PlansStoreSnapshot = DocumentStoreSnapshot[PlanSummary]
NotesStoreSnapshot = DocumentStoreSnapshot[NoteSummary]
ReportsStoreSnapshot = DocumentStoreSnapshot[ReportSummary]


class _DocumentStore(
    BaseStore[DocumentStoreSnapshot[ItemT]],
    Generic[ListSnapT, ItemT, DisplaySnapT],
):
    """Internal generic base shared by PlansStore, NotesStore, ReportsStore.

    Subclasses provide the three field-extractor methods; all cache/subscribe/
    notify logic lives here once.

    Body cache: dict[name → (version_key, markdown)].  Entries are evicted
    when the item's version_key changes on the next list ingest (e.g. a plan's
    revision_count incremented, a note's updated_at changed).  Eviction runs
    eagerly on every list ingest so a stale body is never returned after the
    document changes.
    """

    def __init__(
        self,
        loader: Callable[[str], Awaitable[DisplaySnapT | None]],
    ) -> None:
        super().__init__(
            DocumentStoreSnapshot(items=(), invalidation_key="", selected_name=None, bodies=())
        )
        self._loader = loader
        self._body_cache: dict[str, tuple[str, str]] = {}  # name -> (version_key, markdown)

    # -- subclass contract -------------------------------------------------

    def _unpack_list(self, snapshot: ListSnapT) -> tuple[tuple[ItemT, ...], str]:
        """Return (items, invalidation_key) from the server list snapshot."""
        raise NotImplementedError

    def _item_name(self, item: ItemT) -> str:
        raise NotImplementedError

    def _item_version_key(self, item: ItemT) -> str:
        """Stable content version — revision_count for plans, updated_at for notes/reports."""
        raise NotImplementedError

    def _display_body(self, display: DisplaySnapT) -> str:
        raise NotImplementedError

    def _derive_rows(self, items: tuple[ItemT, ...]) -> tuple[tuple[str, ...], ...]:
        """Return pre-derived display rows for the list widget.

        Each element is a tuple of strings ready to pass directly to DataTable
        add_row.  Subclasses override to provide type-specific projection.
        """
        raise NotImplementedError

    # -- public API --------------------------------------------------------

    def ingest_list(self, snapshot: ListSnapT) -> None:
        """Called by the poll tick; notifies subscribers only when content changed."""
        items, invalidation_key = self._unpack_list(snapshot)
        current = self.get_snapshot()
        if invalidation_key == current.invalidation_key and items == current.items:
            return
        # Evict body cache entries whose version key no longer matches.
        version_map = {self._item_name(it): self._item_version_key(it) for it in items}
        self._body_cache = {
            name: entry
            for name, entry in self._body_cache.items()
            if version_map.get(name) == entry[0]
        }
        self._rebuild(items, invalidation_key, current.selected_name)

    def set_selected(self, name: str | None) -> None:
        """Update the selected document name; notifies if changed."""
        current = self.get_snapshot()
        if name == current.selected_name:
            return
        self._rebuild(current.items, current.invalidation_key, name)

    async def request_body(self, name: str) -> None:
        """Ensure body for ``name`` is loaded; no-op if already cached.

        Calls the injected loader once, caches the result keyed by the item's
        current version_key, then rebuilds the snapshot so subscribers see the
        newly available body.
        """
        if name in self._body_cache:
            return
        display = await self._loader(name)
        if display is None:
            return
        current = self.get_snapshot()
        version_key = next(
            (self._item_version_key(it) for it in current.items if self._item_name(it) == name),
            "",
        )
        self._body_cache[name] = (version_key, self._display_body(display))
        current2 = self.get_snapshot()
        self._rebuild(current2.items, current2.invalidation_key, current2.selected_name)

    # -- internal ----------------------------------------------------------

    def _rebuild(
        self,
        items: tuple[ItemT, ...],
        invalidation_key: str,
        selected_name: str | None,
    ) -> None:
        bodies = tuple(
            sorted(
                ((name, entry[1]) for name, entry in self._body_cache.items()),
                key=lambda x: x[0],
            )
        )
        rows = self._derive_rows(items)
        self._set(
            DocumentStoreSnapshot(
                items=items,
                invalidation_key=invalidation_key,
                selected_name=selected_name,
                bodies=bodies,
                rows=rows,
            )
        )


class PlansStore(_DocumentStore[PlansSnapshot, PlanSummary, PlanDisplaySnapshot]):
    def _unpack_list(self, snap: PlansSnapshot) -> tuple[tuple[PlanSummary, ...], str]:
        return snap.plans, snap.invalidation_key

    def _item_name(self, item: PlanSummary) -> str:
        return item.name

    def _item_version_key(self, item: PlanSummary) -> str:
        return str(item.revision_count)

    def _display_body(self, display: PlanDisplaySnapshot) -> str:
        return display.markdown

    def _derive_rows(self, items: tuple[PlanSummary, ...]) -> tuple[tuple[str, ...], ...]:
        """(display_name, status, revision_count, sync_state) per plan."""
        return tuple(
            (
                item.name.removeprefix("plan-"),
                item.status,
                str(item.revision_count),
                item.sync_state,
            )
            for item in items
        )


class NotesStore(_DocumentStore[NotesSnapshot, NoteSummary, NoteDisplaySnapshot]):
    def _unpack_list(self, snap: NotesSnapshot) -> tuple[tuple[NoteSummary, ...], str]:
        return snap.notes, snap.invalidation_key

    def _item_name(self, item: NoteSummary) -> str:
        return item.name

    def _item_version_key(self, item: NoteSummary) -> str:
        return item.updated_at.isoformat()

    def _display_body(self, display: NoteDisplaySnapshot) -> str:
        return display.markdown

    def _derive_rows(self, items: tuple[NoteSummary, ...]) -> tuple[tuple[str, ...], ...]:
        """(name, char_count, updated_at[:16]) per note (T replaced with space)."""
        return tuple(
            (
                item.name,
                str(item.char_count),
                item.updated_at.isoformat()[:16].replace("T", " "),
            )
            for item in items
        )


class ReportsStore(_DocumentStore[ReportsSnapshot, ReportSummary, ReportDisplaySnapshot]):
    def _unpack_list(self, snap: ReportsSnapshot) -> tuple[tuple[ReportSummary, ...], str]:
        return snap.reports, snap.invalidation_key

    def _item_name(self, item: ReportSummary) -> str:
        return item.name

    def _item_version_key(self, item: ReportSummary) -> str:
        return item.updated_at.isoformat()

    def _display_body(self, display: ReportDisplaySnapshot) -> str:
        return display.markdown

    def _derive_rows(self, items: tuple[ReportSummary, ...]) -> tuple[tuple[str, ...], ...]:
        """(name, char_count, updated_at[:16]) per report (T replaced with space)."""
        return tuple(
            (
                item.name,
                str(item.char_count),
                item.updated_at.isoformat()[:16].replace("T", " "),
            )
            for item in items
        )
