"""Phase 1 acceptance: NDJSON service.log, correlation ids, agent-event sink.

Exercises the Phase 1 criteria that are unit-testable without spawning the real
service: ``configure_logging`` produces valid NDJSON; ``run_id``/``agent_id``
appear only when set; and ``LoggingAgentEventSink`` emits full typed payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from murder.observability.log_context import (
    LogContextFilter,
    log_context,
    set_run_id,
)
from murder.observability.logging_setup import NdjsonFormatter, configure_logging
from murder.runtime.agents.events import (
    AgentDoneEvent,
    AgentFailedEvent,
    AgentNeedsDecisionEvent,
    LoggingAgentEventSink,
)
from murder.state.storage.paths import service_log


def _read_lines(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    return [json.loads(line) for line in text.splitlines() if line]


def _dedicated_logger(path: Path, name: str) -> logging.Logger:
    """A logger wired exactly like the configured root: NDJSON + context filter."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.FileHandler(str(path), encoding="utf-8")
    handler.setFormatter(NdjsonFormatter())
    handler.addFilter(LogContextFilter())
    logger.addHandler(handler)
    return logger


def test_service_log_is_valid_ndjson_with_core_fields(tmp_path: Path) -> None:
    log_path = service_log(tmp_path, "run-001")
    configure_logging(level="INFO", log_path=log_path)
    logging.getLogger("murder.acceptance.core").info("a structured line")

    for h in logging.getLogger().handlers:
        h.flush()

    lines = _read_lines(log_path)
    assert lines, "expected at least one line in service.log"
    for obj in lines:
        # Every line parses cleanly and carries the four core fields.
        assert "ts" in obj
        assert "level" in obj
        assert "logger" in obj
        assert "msg" in obj


def test_correlation_ids_present_only_when_set(tmp_path: Path) -> None:
    log_path = tmp_path / "ctx.log"
    logger = _dedicated_logger(log_path, "murder.acceptance.ctx")

    from murder.observability import log_context as ctx

    # No ids set: keys must be ABSENT (not null).
    logger.info("before run")

    set_run_id("run-XYZ")
    try:
        with log_context(agent_id="agent-7"):
            logger.info("inside run with agent")
        # agent_id was scoped to the context; run_id persists (no reset on set_run_id).
        logger.info("after agent context")

        for h in logger.handlers:
            h.flush()
    finally:
        ctx._run_id.set(None)  # don't leak run_id into sibling tests

    lines = _read_lines(log_path)
    before, inside, after = lines[0], lines[1], lines[2]

    assert "run_id" not in before
    assert "agent_id" not in before

    assert inside["run_id"] == "run-XYZ"
    assert inside["agent_id"] == "agent-7"

    assert after["run_id"] == "run-XYZ"
    assert "agent_id" not in after


def test_logging_sink_emits_full_typed_payload(tmp_path: Path) -> None:
    log_path = tmp_path / "sink.log"
    logger = _dedicated_logger(log_path, "murder.acceptance.sink")
    sink = LoggingAgentEventSink(logger=logger)

    ts = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
    asyncio.run(sink.emit(AgentFailedEvent("sess-a", "boom went wrong", ts)))
    asyncio.run(sink.emit(AgentDoneEvent("sess-b", "merged", ts)))
    asyncio.run(
        sink.emit(AgentNeedsDecisionEvent("sess-c", "pick one?", ["x", "y"], ts))
    )

    for h in logger.handlers:
        h.flush()

    failed, done, decision = _read_lines(log_path)

    # Failed -> WARNING, full payload as structured fields.
    assert failed["level"] == "WARNING"
    assert failed["event_type"] == "AgentFailedEvent"
    assert failed["session_name"] == "sess-a"
    assert failed["error"] == "boom went wrong"
    assert "timestamp" in failed
    assert "sess-a" in failed["msg"]

    assert done["level"] == "INFO"
    assert done["event_type"] == "AgentDoneEvent"
    assert done["outcome"] == "merged"

    assert decision["event_type"] == "AgentNeedsDecisionEvent"
    assert decision["question"] == "pick one?"
    assert decision["choices"] == ["x", "y"]


def test_sink_remaps_reserved_message_field(tmp_path: Path) -> None:
    """A field named ``message`` must be remapped, not crash the ``extra=`` call."""
    from murder.runtime.agents.events import AgentMessageEvent

    log_path = tmp_path / "msg.log"
    logger = _dedicated_logger(log_path, "murder.acceptance.msg")
    sink = LoggingAgentEventSink(logger=logger)

    ts = datetime(2026, 6, 13, tzinfo=timezone.utc)
    asyncio.run(sink.emit(AgentMessageEvent("sess-m", "hello there", ts)))

    for h in logger.handlers:
        h.flush()

    (obj,) = _read_lines(log_path)
    assert obj["event_message"] == "hello there"
    assert obj["msg"] != "hello there"  # core msg is the human string
    assert obj["session_name"] == "sess-m"
