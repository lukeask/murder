"""Built-in RPC handlers, grouped by namespace.

``register_all(host)`` populates ``host._rpc_handlers`` with every default
handler, dispatching to one module per RPC namespace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from murder.app.service.handlers import (
    approvals,
    command,
    harness_control,
    health,
    image,
    plan,
    sessions,
    settings,
    state,
    ticket,
    trigger,
    tui,
    worktree,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register_all(host: ServiceHost) -> None:
    approvals.register(host)
    health.register(host)
    harness_control.register(host)
    command.register(host)
    state.register(host)
    sessions.register(host)
    ticket.register(host)
    plan.register(host)
    image.register(host)
    tui.register(host)
    trigger.register(host)
    settings.register(host)
    worktree.register(host)
