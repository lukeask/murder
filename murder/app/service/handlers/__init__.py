"""Built-in feature handlers, grouped by namespace.

``register_all(host)`` connects feature use cases directly to closed
application capability enums.
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
    roster,
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
    roster.register(host)
    sessions.register(host)
    ticket.register(host)
    plan.register(host)
    image.register(host)
    tui.register(host)
    workflows.register(host)
    trigger.register(host)
    settings.register(host)
    worktree.register(host)
