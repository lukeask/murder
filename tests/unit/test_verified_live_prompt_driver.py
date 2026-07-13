"""Live verified prompt traces using only the actuator-backed fake tmux."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from murder.llm.harness_control.model import InputChunk, InputProvenance, OperationOutcome
from murder.llm.harness_control.runtime.prompt_driver import PromptDriverPolicy
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.state.persistence.schema import init_db
from tests.support.fake_tmux import FakeTmux

INITIAL = "› \n"
PAYLOAD_VISIBLE = "› hello\n"
ACKNOWLEDGED = "› hello\n• Working (1s • esc to interrupt)\n"


def _session(fake_tmux: FakeTmux) -> tuple[VerifiedHarnessControlSession, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    fake_tmux.set_pane_dimensions(177, 61)
    session = VerifiedHarnessControlSession.from_tmux(
        harness_kind="codex",
        terminal_session="verified-pane",
        connection=connection,
        persistence_session_id="agent-verified",
    )
    session._prompt_driver._policy = PromptDriverPolicy(  # type: ignore[attr-defined]
        observation_interval=timedelta(), maximum_observations=12
    )
    return session, connection


async def _no_sleep(_: float) -> None:
    return None


def test_enter_effect_requires_observed_acknowledgment_and_persists_frame_provenance(
    fake_tmux: FakeTmux,
) -> None:
    session, connection = _session(fake_tmux)
    session._prompt_driver._sleep = _no_sleep  # type: ignore[attr-defined]
    fake_tmux.queue_pane(INITIAL)
    fake_tmux.queue_pane_after_effect(
        PAYLOAD_VISIBLE, effect="paste_buffer_literal", effect_text="hello"
    )
    fake_tmux.queue_pane_after_effect(ACKNOWLEDGED, effect="send_keys", effect_text="Enter")

    result = __import__("asyncio").run(
        session.submit_prompt((InputChunk("hello", InputProvenance.USER_PASTE_BLOCK, "paste-1"),))
    )

    assert result.outcome is OperationOutcome.SUBMITTED
    enter_calls = [args for args, _ in fake_tmux.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enter_calls) == 1
    frames = connection.execute(
        "SELECT width, height, ansi_preserved, capture_sequence, raw_text "
        "FROM harness_control_frames ORDER BY capture_sequence"
    ).fetchall()
    assert frames
    assert all(
        (row["width"], row["height"], row["ansi_preserved"]) == (177, 61, 1) for row in frames
    )
    assert frames[0]["capture_sequence"] == 1
    assert frames[0]["raw_text"] == INITIAL


def test_send_and_enter_alone_do_not_become_semantic_submission_success(
    fake_tmux: FakeTmux,
) -> None:
    session, _connection = _session(fake_tmux)
    session._prompt_driver._sleep = _no_sleep  # type: ignore[attr-defined]
    session._prompt_driver._policy = PromptDriverPolicy(  # type: ignore[attr-defined]
        observation_interval=timedelta(), maximum_observations=9
    )
    fake_tmux.queue_pane(INITIAL)
    fake_tmux.queue_pane_after_effect(
        PAYLOAD_VISIBLE, effect="paste_buffer_literal", effect_text="hello"
    )

    result = __import__("asyncio").run(
        session.submit_prompt((InputChunk("hello", InputProvenance.USER_PASTE_BLOCK, "paste-1"),))
    )

    assert result.outcome is OperationOutcome.ESCALATED
    enter_calls = [args for args, _ in fake_tmux.calls_to("send_keys") if args[1] == "Enter"]
    assert len(enter_calls) == 1
