from __future__ import annotations

from pathlib import Path

import pytest

from murder.state.storage.service_registry import (
    AmbiguousServiceSessionError,
    ServiceSession,
    project_path_hash,
    project_session_name,
    resolve_service_session_selector,
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


def test_resolve_service_session_selector_rejects_duplicate_basenames(tmp_path: Path) -> None:
    first = ServiceSession(
        name="project-aaaaaaaaaaaa",
        basename="project",
        path_hash="aaaaaaaaaaaa",
        repo_root=tmp_path / "kenny" / "project",
        pid=1001,
        websocket_url="ws://127.0.0.1:9001/api/ws",
    )
    second = ServiceSession(
        name="project-bbbbbbbbbbbb",
        basename="project",
        path_hash="bbbbbbbbbbbb",
        repo_root=tmp_path / "tony" / "project",
        pid=1002,
        websocket_url="ws://127.0.0.1:9002/api/ws",
    )

    with pytest.raises(AmbiguousServiceSessionError) as exc_info:
        resolve_service_session_selector("project", [first, second])

    assert [match.name for match in exc_info.value.matches] == [first.name, second.name]
    assert resolve_service_session_selector(first.name, [first, second]) == first
