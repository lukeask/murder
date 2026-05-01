"""Discriminated union: AgentEvent serialization round-trips."""

from __future__ import annotations

import pytest


def test_heartbeat_serializes_with_type_field() -> None:
    # TODO(M2): construct HeartbeatEvent; .model_dump() has type='heartbeat';
    # round-trip via TypeAdapter[AgentEvent].validate_python.
    pytest.skip("M2 stub")


def test_question_event_carries_monkey_session() -> None:
    # TODO(M2): assert presence of monkey_session field for D3 routing.
    pytest.skip("M2 stub")


def test_event_filter_matches_role() -> None:
    # TODO(M2)
    pytest.skip("M2 stub")
