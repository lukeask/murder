"""``worktree.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.reads import EmptyParams, WorktreeEntry, WorktreesListResult
from murder.app.protocol.requests import QueryName
from murder.app.service.handlers._common import threaded

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _worktree_list(body: dict[str, Any]) -> dict[str, Any]:
        from murder.state.storage.worktrees import list_murder_worktrees_sync

        EmptyParams.model_validate(body or {})
        entries = list_murder_worktrees_sync(host.repo_root)
        return WorktreesListResult(
            ok=True,
            entries=[
                WorktreeEntry(
                    path=str(entry.path),
                    branch=entry.branch,
                    is_main=entry.is_main,
                )
                for entry in entries
            ],
        ).model_dump(mode="json")

    # Pure git subprocess + file reads, no shared connection — offloaded.
    host.register_application_query(QueryName.WORKTREES_LIST, threaded(_worktree_list))
