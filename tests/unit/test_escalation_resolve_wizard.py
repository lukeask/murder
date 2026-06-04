"""Spawn-style inline escalation resolver."""

from __future__ import annotations

from murder.app.service.client_api import EscalationSummary
from murder.app.tui.escalation_resolve_wizard import EscalationResolveWizard


def _row(*, ticket_status: str | None = None) -> EscalationSummary:
    return EscalationSummary(
        id=9,
        ticket_id="t-9",
        severity=2,
        reason="needs user decision",
        to_recipient="user",
        body_path=None,
        ticket_status=ticket_status,
    )


def test_resolver_renders_ack_first_and_navigation_controls() -> None:
    wizard = EscalationResolveWizard(_row())
    wizard.on_mount()

    rendered = str(wizard._display.render())

    assert "Resolve escalation #9" in rendered
    assert "Acknowledge / mark resolved" in rendered
    assert "Open affected view" in rendered
    assert "Retry failed ticket" not in rendered
    assert "Enter confirms" in rendered


def test_resolver_includes_retry_ack_for_failed_ticket() -> None:
    wizard = EscalationResolveWizard(_row(ticket_status="failed"))
    wizard.on_mount()

    rendered = str(wizard._display.render())

    assert "Retry failed ticket and mark resolved" in rendered


def test_resolver_confirms_selected_action(monkeypatch) -> None:
    wizard = EscalationResolveWizard(_row(ticket_status="failed"))
    posted: list[EscalationResolveWizard.Confirmed] = []
    monkeypatch.setattr(wizard, "post_message", posted.append)
    wizard.action_cursor_down()

    wizard.action_confirm()

    assert [(message.escalation.id, message.action) for message in posted] == [(9, "retry_ack")]
