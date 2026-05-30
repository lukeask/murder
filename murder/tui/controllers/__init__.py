"""TUI controller layer — action/command handlers separated from widget composition.

Implemented:
  DispatchController  - ticket/schedule/usage command dispatch (dispatch.py)

Intentionally deferred (widget entanglement makes clean extraction premature):
  PlanningController  - collaborator chat, note operations interleave with widget state
  CrowsController     - session-sync helpers require mirror widget ref
  SettingsController  - settings screen save/reload reads many App-level properties
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Coroutine

SubmitCommandFn = Callable[..., "Coroutine[Any, Any, dict[str, object] | None]"]

if TYPE_CHECKING:
    from murder.service.client_api import TicketCarveSnapshot

GetTicketStatusFn = Callable[[str], "Coroutine[Any, Any, str | None]"]
GetTicketCarveFn = Callable[[str], "Coroutine[Any, Any, TicketCarveSnapshot | None]"]


@dataclass
class TuiContext:
    """Minimal app capabilities shared by domain controllers."""

    submit_command: SubmitCommandFn
    notify: Callable[..., None]
    refresh_views: Callable[[], None]
    push_screen: Callable[..., Any]
    run_worker: Callable[..., Any]
    get_ticket_status: GetTicketStatusFn
    get_ticket_carve_snapshot: GetTicketCarveFn


from murder.tui.controllers.dispatch import DispatchController

__all__ = ["DispatchController", "TuiContext"]
