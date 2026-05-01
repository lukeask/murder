"""Status transition rules (D7)."""

from __future__ import annotations

import pytest

from murder.bus import TicketStatus
from murder.tickets.lifecycle import VALID_TRANSITIONS


def test_done_can_reopen_to_planned() -> None:
    """D7: 'we were wrong' path."""
    assert TicketStatus.PLANNED in VALID_TRANSITIONS[TicketStatus.DONE]


def test_failed_to_planned_only() -> None:
    assert VALID_TRANSITIONS[TicketStatus.FAILED] == {TicketStatus.PLANNED}


def test_planned_to_in_progress_is_blocked() -> None:
    """planned must pass through ready first."""
    assert TicketStatus.IN_PROGRESS not in VALID_TRANSITIONS[TicketStatus.PLANNED]


def test_invalid_transition_raises(memdb: object) -> None:
    # TODO(M4): seed a ticket; call transition() with disallowed target; expect InvalidTransition.
    pytest.skip("M4 stub")


def test_reopen_cascades_dependents(memdb: object) -> None:
    # TODO(M4): seed t1 done, t2 ready depends_on t1; reopen(t1); assert t2 → planned.
    pytest.skip("M4 stub")
