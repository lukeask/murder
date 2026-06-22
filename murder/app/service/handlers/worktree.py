"""``worktree.*`` RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.service.handlers._common import threaded

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _worktree_list(_body: dict[str, Any]) -> dict[str, Any]:
        from murder.state.storage.worktrees import list_murder_worktrees_sync

        entries = list_murder_worktrees_sync(host.repo_root)
        return {
            "ok": True,
            "entries": [
                {
                    "path": str(entry.path),
                    "branch": entry.branch,
                    "is_main": entry.is_main,
                }
                for entry in entries
            ],
        }

    # Pure git subprocess + file reads, no shared connection — offloaded.
    host.register_rpc_handler("worktree.list", threaded(_worktree_list))
