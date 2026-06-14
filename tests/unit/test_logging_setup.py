"""Phase 1 logging substrate: NDJSON formatter, idempotency, level resolution, context."""

from __future__ import annotations

import json
import logging

import pytest

from murder.observability import log_context as ctx
from murder.observability.logging_setup import (
    DEFAULT_LEVEL,
    NdjsonFormatter,
    configure_logging,
    resolve_log_level,
)


def _make_record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="murder.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_formatter_emits_single_json_object_with_core_fields() -> None:
    line = NdjsonFormatter().format(_make_record("a message"))
    assert "\n" not in line
    obj = json.loads(line)
    assert obj["msg"] == "a message"
    assert obj["level"] == "INFO"
    assert obj["logger"] == "murder.test"
    assert "ts" in obj


def test_formatter_includes_run_id_when_set_and_omits_when_unset() -> None:
    fmt = NdjsonFormatter()
    filt = ctx.LogContextFilter()

    rec_unset = _make_record()
    filt.filter(rec_unset)
    assert "run_id" not in json.loads(fmt.format(rec_unset))

    with ctx.log_context(run_id="run-123"):
        rec_set = _make_record()
        filt.filter(rec_set)
        obj = json.loads(fmt.format(rec_set))
    assert obj["run_id"] == "run-123"


def test_formatter_renders_exc_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="murder.test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    obj = json.loads(NdjsonFormatter().format(record))
    assert "ValueError: boom" in obj["exc"]


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    saved = list(root.handlers)
    saved_level = root.level
    root.handlers = []
    yield root
    root.handlers = saved
    root.setLevel(saved_level)


def _stderr_handlers(root):
    from murder.observability.logging_setup import _STDERR_TAG

    return [h for h in root.handlers if getattr(h, _STDERR_TAG, False)]


def test_configure_logging_idempotent_stderr(clean_root) -> None:
    configure_logging(level="INFO", log_path=None)
    configure_logging(level="INFO", log_path=None)
    assert len(_stderr_handlers(clean_root)) == 1


def test_configure_logging_attaches_file_handler_on_later_call(clean_root, tmp_path) -> None:
    configure_logging(level="INFO", log_path=None)
    assert len(_stderr_handlers(clean_root)) == 1
    assert not [h for h in clean_root.handlers if isinstance(h, logging.FileHandler)]

    log_path = tmp_path / "nested" / "service.log"
    configure_logging(level="INFO", log_path=log_path)
    file_handlers = [h for h in clean_root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert log_path.exists() or log_path.parent.exists()

    # Calling again with the same path does not duplicate the file handler.
    configure_logging(level="INFO", log_path=log_path)
    file_handlers = [h for h in clean_root.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1


def test_configure_logging_sets_level(clean_root) -> None:
    configure_logging(level="DEBUG", log_path=None)
    assert clean_root.level == logging.DEBUG


def test_configure_logging_invalid_level_falls_back(clean_root) -> None:
    configure_logging(level="LOUD", log_path=None)
    assert clean_root.level == logging.INFO


def test_resolve_log_level_precedence(monkeypatch) -> None:
    monkeypatch.setenv("MURDER_LOG_LEVEL", "WARNING")
    # CLI beats env.
    assert resolve_log_level("DEBUG") == "DEBUG"
    # Env beats config/default when no CLI.
    assert resolve_log_level(None) == "WARNING"

    monkeypatch.delenv("MURDER_LOG_LEVEL", raising=False)
    # Config beats default.
    monkeypatch.setattr(
        "murder.observability.logging_setup._config_log_level", lambda: "ERROR"
    )
    assert resolve_log_level(None) == "ERROR"

    # Default when nothing set.
    monkeypatch.setattr(
        "murder.observability.logging_setup._config_log_level", lambda: None
    )
    assert resolve_log_level(None) == DEFAULT_LEVEL


def test_resolve_log_level_invalid_falls_back(monkeypatch) -> None:
    monkeypatch.delenv("MURDER_LOG_LEVEL", raising=False)
    monkeypatch.setattr(
        "murder.observability.logging_setup._config_log_level", lambda: None
    )
    assert resolve_log_level("nonsense") == DEFAULT_LEVEL
    assert resolve_log_level("debug") == "DEBUG"  # case-insensitive


def test_log_context_sets_and_resets() -> None:
    assert ctx._run_id.get() is None
    with ctx.log_context(run_id="r1", agent_id="a1", command_id="c1", event_id="e1"):
        assert ctx._run_id.get() == "r1"
        assert ctx._agent_id.get() == "a1"
        assert ctx._command_id.get() == "c1"
        assert ctx._event_id.get() == "e1"
    assert ctx._run_id.get() is None
    assert ctx._agent_id.get() is None
    assert ctx._command_id.get() is None
    assert ctx._event_id.get() is None


def test_log_context_ignores_unknown_keys() -> None:
    with ctx.log_context(bogus="x", run_id="r"):
        assert ctx._run_id.get() == "r"
    assert ctx._run_id.get() is None


def test_log_context_nested_same_key() -> None:
    with ctx.log_context(run_id="outer"):
        with ctx.log_context(run_id="inner"):
            assert ctx._run_id.get() == "inner"
        assert ctx._run_id.get() == "outer"
    assert ctx._run_id.get() is None


def test_set_run_id_persists() -> None:
    token = ctx._run_id.set(None)
    try:
        ctx.set_run_id("svc-run")
        assert ctx._run_id.get() == "svc-run"
    finally:
        ctx._run_id.reset(token)


def test_config_has_log_level_field() -> None:
    from murder.user_config import UserConfig

    assert UserConfig().log_level == "INFO"


def test_service_log_path() -> None:
    from pathlib import Path

    from murder.state.storage.paths import service_log

    p = service_log(Path("/repo"), "20260101")
    assert p == Path("/repo/.murder/runs/20260101/service.log")
