from __future__ import annotations

import os
from pathlib import Path

import pytest

from murder.state.storage.service_registry import (
    daemon_registry_path,
    live_daemon_record,
    project_path_hash,
    project_session_name,
    read_daemon_record,
    service_runtime_root,
    write_daemon_record,
)


def test_project_session_name_uses_resolved_full_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    kenny = tmp_path / "home" / "kenny" / "project"
    tony = tmp_path / "home" / "tony" / "project"
    kenny.mkdir(parents=True)
    tony.mkdir(parents=True)

    kenny_name = project_session_name(kenny)
    tony_name = project_session_name(tony)

    assert kenny_name == f"project-{project_path_hash(kenny)}"
    assert tony_name == f"project-{project_path_hash(tony)}"
    assert kenny_name != tony_name


def test_write_and_read_daemon_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    record = write_daemon_record(port=62077, pid=4242, started_at="2026-01-01T00:00:00+00:00")
    assert record.pid == 4242
    assert record.port == 62077
    assert daemon_registry_path() == service_runtime_root() / "daemon.json"
    loaded = read_daemon_record()
    assert loaded == record


def test_live_daemon_record_clears_dead_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    write_daemon_record(port=62077, pid=2_000_000_001)
    assert live_daemon_record() is None
    assert read_daemon_record() is None


def test_service_runtime_root_uses_explicit_xdg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert service_runtime_root() == runtime / "murder"


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_service_runtime_root_treats_empty_xdg_as_unset(
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", value)
    user_runtime = tmp_path / "run-user"
    user_runtime.mkdir()
    monkeypatch.setattr(
        "murder.state.storage.service_registry._default_user_runtime_dir",
        lambda: user_runtime,
    )
    assert service_runtime_root() == user_runtime / "murder"


def test_service_runtime_root_falls_back_to_tmp_when_no_user_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(
        "murder.state.storage.service_registry._default_user_runtime_dir",
        lambda: None,
    )
    assert service_runtime_root() == Path(f"/tmp/murder-{os.getuid()}")
