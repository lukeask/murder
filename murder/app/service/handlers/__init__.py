"""Built-in feature handlers, grouped by namespace.

``register_all(host)`` populates ``host._rpc_handlers`` with every default
handler. The application composition root resolves these functions once into
enum-keyed direct dispatch; the string registry remains only for old RPC
consumers during retirement.
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
    workflows,
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
    workflows.register(host)
    trigger.register(host)
    settings.register(host)
    worktree.register(host)
