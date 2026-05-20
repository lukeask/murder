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

if TYPE_CHECKING:
    from murder.service.read_model import ServiceReadModel

SubmitCommandFn = Callable[..., "Coroutine[Any, Any, dict[str, object] | None]"]


@dataclass
class TuiContext:
    """Minimal app capabilities shared by domain controllers."""

    submit_command: SubmitCommandFn
    notify: Callable[..., None]
    refresh_views: Callable[[], None]
    push_screen: Callable[..., Any]
    run_worker: Callable[..., Any]
    read_model: "ServiceReadModel"


from murder.tui.controllers.dispatch import DispatchController

__all__ = ["DispatchController", "TuiContext"]
