"""Transit-panel snapshot builder: per-lane git commit graph."""

from __future__ import annotations

from murder.state.storage.git_transit import TransitSnapshot, build_transit_snapshot

from ._common import ReadModelBase


class TransitReadModel(ReadModelBase):
    """Build the Transit panel's git commit-graph snapshot."""

    def get_transit_snapshot(self) -> TransitSnapshot:
        """Build the per-lane git commit-graph for the Transit panel.

        Derived from git on demand (``main`` + ``.murder/worktrees`` branches),
        not persisted. ``repo_root`` is recovered from ``db_path`` (which is
        ``<repo_root>/.murder/murder.db``). The fingerprint doubles as the
        ``invalidation_key`` so the poll loop's change detection and the
        client's refetch keying agree.
        """
        repo_root = self.db_path.parent.parent
        return build_transit_snapshot(repo_root)
