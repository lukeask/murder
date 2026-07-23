"""Tests for the Phase 2 advanced flight-recorder substrate."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from murder.observability.advanced_log import (
    AdvancedLog,
    ApiRecord,
    ChangeGate,
    NullAdvancedLog,
    TmuxFrameRecord,
    open_advanced_log,
    redact,
)
from murder.observability.log_context import log_context
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import advlogs_dir, db_path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def test_null_writer_creates_no_file(tmp_path):
    repo = _repo(tmp_path)
    log = open_advanced_log(repo, "run-1", "off")
    assert isinstance(log, NullAdvancedLog)
    # No-ops do nothing and never raise.
    log.record_api(ApiRecord(request={"a": 1}, model="m"))
    log.record_tmux_frame(TmuxFrameRecord(session="s", op="capture", frame="x"))
    assert not advlogs_dir(repo).exists()


def test_redacted_mode_creates_db_with_schema_and_session_info(tmp_path):
    repo = _repo(tmp_path)
    conn = get_db(db_path(repo))
    init_db(conn)
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-2", "2026-01-01T00:00:00", '{"x": 1}'),
    )
    conn.commit()

    async def _run():
        log = open_advanced_log(repo, "run-2", "redacted")
        await log.start()
        log.write_session_info(main_db=conn)
        await log.stop()
        return log._db_path

    path = asyncio.run(_run())
    assert path.exists()
    advconn = sqlite3.connect(str(path))
    advconn.row_factory = sqlite3.Row
    ver = advconn.execute("SELECT version FROM schema_version").fetchone()["version"]
    assert ver == 1
    row = advconn.execute("SELECT * FROM session_info").fetchone()
    assert row["run_id"] == "run-2"
    assert row["mode"] == "redacted"
    assert "retained_facts.v1=" in row["main_schema"]
    assert "runs.advanced_log_path=" in row["main_schema"]
    advconn.close()


def test_raw_filename_contains_raw(tmp_path):
    repo = _repo(tmp_path)
    log = open_advanced_log(repo, "run-3", "raw")
    assert "RAW" in log._db_path.name
    asyncio.run(log.stop())


def test_redact_replaces_secrets_keeps_structure():
    obj = {
        "Authorization": "Bearer sk-secretvalue123456",
        "api_key": "abc123",
        "model": "gpt-5",
        "status": 200,
        "run_id": "r1",
        "nested": {"password": "hunter2", "ok": "fine"},
    }
    out = redact(obj)
    assert out["Authorization"]["__redacted__"] is True
    assert out["api_key"]["__redacted__"] is True
    assert out["nested"]["password"]["__redacted__"] is True
    # Non-secrets preserved.
    assert out["model"] == "gpt-5"
    assert out["status"] == 200
    assert out["run_id"] == "r1"
    assert out["nested"]["ok"] == "fine"


def test_raw_mode_passes_secret_through(tmp_path):
    repo = _repo(tmp_path)

    async def _run():
        log = open_advanced_log(repo, "run-raw", "raw")
        await log.start()
        log.record_api(
            ApiRecord(request={"Authorization": "Bearer sk-rawtoken12345678"}, model="m")
        )
        await log.stop()
        return log._db_path

    path = asyncio.run(_run())
    advconn = sqlite3.connect(str(path))
    payload = advconn.execute("SELECT payload FROM api_records").fetchone()[0]
    assert "sk-rawtoken12345678" in payload
    advconn.close()


def test_change_gate_dedups_and_cadence():
    clock = [0.0]
    gate = ChangeGate(clock=lambda: clock[0])
    assert gate.should_record("s", "h1") is True
    assert gate.should_record("s", "h1") is False  # unchanged, no cadence
    assert gate.suppressed == 1
    assert gate.should_record("s", "h2") is True  # changed
    # cadence override: unchanged but interval elapsed
    assert gate.should_record("k", "x", min_interval_s=1.0) is True
    assert gate.should_record("k", "x", min_interval_s=1.0) is False
    clock[0] = 2.0
    assert gate.should_record("k", "x", min_interval_s=1.0) is True


class _FakeBusEvent:
    """A minimal event for record_bus_event: default family, vars()-serializable."""

    record_family = "event_records"

    def __init__(self) -> None:
        self.hello = "world"


def test_record_bus_event_enqueues_with_correlation_ids(tmp_path):
    repo = _repo(tmp_path)

    async def _run():
        log = open_advanced_log(repo, "run-corr", "redacted")
        await log.start()
        # The recorder reads the ambient correlation context itself — the bus
        # subscriber runs inside the publisher's log_context in production.
        with log_context(run_id="run-corr", agent_id="ag-1", command_id="cmd-1"):
            log.record_bus_event(_FakeBusEvent())
        await log.stop()
        return log._db_path

    path = asyncio.run(_run())
    advconn = sqlite3.connect(str(path))
    advconn.row_factory = sqlite3.Row
    row = advconn.execute("SELECT * FROM event_records").fetchone()
    assert row["run_id"] == "run-corr"
    assert row["agent_id"] == "ag-1"
    assert row["command_id"] == "cmd-1"
    assert row["capture_level"] == "redacted"
    assert "world" in row["payload"]
    advconn.close()
