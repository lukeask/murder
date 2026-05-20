"""DispatchController — ticket/schedule/usage command dispatch for the TUI.

Owns all command submission that touches the service layer, hiding command
kinds, payloads, and response shapes from MurderApp.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from murder.harnesses import REGISTRY
from murder.usage_sample_command import (
    HARNESS_USAGE_SAMPLE_KIND,
    TRIGGER_USAGE_MANUAL_KEY,
    USAGE_PROBE_TARGET,
    USAGE_SAMPLE_DEFAULT_TIMEOUT_S,
    harness_usage_sample_payload,
)
from murder_newstructure.tui.dispatch import CarveFormScreen

if TYPE_CHECKING:
    from murder_newstructure.tui.controllers import TuiContext

_SCHEDULE_USAGE_DEBOUNCE_S = 20.0


class DispatchController:
    """Submits ticket/schedule/usage commands; owns debounce state for usage probing."""

    def __init__(self, ctx: TuiContext) -> None:
        self._ctx = ctx
        self._last_usage_probe_at: float | None = None

    async def kick_ready(self) -> None:
        result = await self._ctx.submit_command(
            target_worker="orchestrator",
            kind="scheduler.kickoff_ready",
            payload={},
            timeout_s=30.0,
        )
        if result is None:
            return
        kicked = list(result.get("kicked", []))
        self._ctx.notify(
            f"kicked: {', '.join(kicked)}" if kicked else "no ready tickets",
            timeout=3,
        )
        self._ctx.refresh_views()

    async def set_scheduler_mode(self, to_mode: str) -> None:
        result = await self._ctx.submit_command(
            target_worker="scheduler",
            kind="scheduler.set_mode",
            payload={"mode": to_mode},
            timeout_s=10.0,
        )
        if result is None:
            return
        self._ctx.notify(f"Scheduler mode → {to_mode}", timeout=3)
        self._ctx.refresh_views()

    async def retry_failed(self, ticket_id: str) -> None:
        result = await self._ctx.submit_command(
            target_worker="orchestrator",
            kind="ticket.retry_failed",
            payload={"ticket_id": ticket_id},
            timeout_s=15.0,
        )
        if result is None:
            return
        self._ctx.notify(f"{ticket_id} queued for retry; status=planned", timeout=4)
        self._ctx.refresh_views()

    async def update_metadata_and_status(
        self,
        ticket_id: str,
        spec: dict[str, object],
        *,
        notify_success: bool = True,
    ) -> None:
        meta_result = await self._ctx.submit_command(
            target_worker="orchestrator",
            kind="ticket.update_metadata",
            payload={"ticket_id": ticket_id, **spec},
            timeout_s=30.0,
        )
        if meta_result is None:
            return
        if not meta_result.get("ok"):
            self._ctx.notify(
                str(meta_result.get("error") or "metadata update failed"),
                severity="error",
                timeout=10,
            )
            return
        db_status = self._ctx.read_model.get_ticket_status(ticket_id) or ""
        want = str(spec.get("status") or "").strip()
        if not want:
            want = db_status
        if want and want != db_status:
            status_result = await self._ctx.submit_command(
                target_worker="orchestrator",
                kind="ticket.force_status",
                payload={"ticket_id": ticket_id, "status": want},
                timeout_s=15.0,
            )
            if status_result is None:
                return
            if not status_result.get("ok"):
                self._ctx.notify(
                    str(status_result.get("error") or "status update failed"),
                    severity="error",
                    timeout=10,
                )
                return
        if notify_success:
            self._ctx.notify(f"{ticket_id} updated", timeout=4)
        self._ctx.refresh_views()

    def enqueue_carve_autosave(self, ticket_id: str, spec: dict[str, object]) -> None:
        self._ctx.run_worker(
            self.update_metadata_and_status(ticket_id, spec, notify_success=False),
            exclusive=True,
            group="carve",
        )

    def open_carve_screen(self, ticket_id: str) -> None:
        carve = self._ctx.read_model.get_ticket_carve_snapshot(ticket_id)
        if carve is None:
            self._ctx.notify(f"ticket {ticket_id} not found", severity="error", timeout=4)
            return
        hint = "[dim]Known harness kinds:[/dim] " + ", ".join(sorted(REGISTRY.keys()))
        self._ctx.push_screen(
            CarveFormScreen(
                carve,
                harness_hint=hint,
                on_autosave=lambda spec: self.enqueue_carve_autosave(ticket_id, spec),
            ),
            lambda _result: None,
        )

    async def probe_usage_on_schedule_enter(self) -> None:
        now = time.monotonic()
        if (
            self._last_usage_probe_at is not None
            and now - self._last_usage_probe_at < _SCHEDULE_USAGE_DEBOUNCE_S
        ):
            return
        self._last_usage_probe_at = now
        result = await self._ctx.submit_command(
            target_worker="usage-probe",
            kind="scheduler.probe_usage",
            payload={"trigger": "schedule_view_enter"},
            timeout_s=20.0,
        )
        if result is None:
            return
        stored = int(result.get("stored", 0))
        failures = int(result.get("failures", 0))
        self._ctx.refresh_views()
        if stored or failures:
            self._ctx.notify(
                f"Schedule usage: {stored} ok, {failures} failed",
                timeout=4,
            )

    async def collect_usage_snapshots(self) -> None:
        result = await self._ctx.submit_command(
            target_worker=USAGE_PROBE_TARGET,
            kind=HARNESS_USAGE_SAMPLE_KIND,
            payload=harness_usage_sample_payload(trigger=TRIGGER_USAGE_MANUAL_KEY),
            timeout_s=USAGE_SAMPLE_DEFAULT_TIMEOUT_S,
        )
        if result is None:
            return
        stored = int(result.get("stored", 0))
        failures = int(result.get("failures", 0))
        self._ctx.refresh_views()
        if stored or failures:
            self._ctx.notify(
                f"Sampled {stored} harness usages ({failures} failed).",
                timeout=4,
            )


__all__ = ["DispatchController"]
