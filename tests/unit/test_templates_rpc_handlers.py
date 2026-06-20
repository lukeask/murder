"""Userspace text-template registry: user_config helpers + host RPC handlers.

The ``tui.{load,save}_templates`` handlers persist a global ``templates.yaml`` under
the XDG config home (NOT the per-repo prefs file). We point ``XDG_CONFIG_HOME`` at a
tmp dir so each test is isolated, and assert the normalization/atomic-write contract the
frontend is built against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.app.service.host import ServiceHost
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.user_config import (
    load_templates,
    save_templates,
    templates_path,
)


def _host(repo_root: Path) -> ServiceHost:
    config = Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )
    host = ServiceHost(config=config, repo_root=repo_root)
    host.register_default_rpc_handlers()
    return host


def _call(host: ServiceHost, method: str, body: dict) -> dict:
    return host._rpc_handlers[method](body)  # type: ignore[return-value]


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


# --- user_config helpers -------------------------------------------------


def test_load_missing_returns_empty(xdg: Path) -> None:
    assert not templates_path().exists()
    assert load_templates() == []


def test_save_then_load_round_trips(xdg: Path) -> None:
    save_templates([{"name": "foo", "body": "multi\nline\n"}])
    assert load_templates() == [{"name": "foo", "body": "multi\nline\n"}]


def test_normalization_invalid_dropped_dupes_collapsed_sorted(xdg: Path) -> None:
    out = save_templates(
        [
            {"name": "zed", "body": "z"},
            {"name": "bad name", "body": "x"},  # invalid -> dropped
            {"name": "", "body": "x"},  # empty -> dropped
            {"name": "dup", "body": "first"},
            {"name": "dup", "body": "last"},  # last wins
            {"name": "abc", "body": "a"},
            "not a dict",  # ignored
        ]
    )
    assert out == [
        {"name": "abc", "body": "a"},
        {"name": "dup", "body": "last"},
        {"name": "zed", "body": "z"},
    ]
    # Return value is the canonical persisted state.
    assert load_templates() == out


def test_body_coerced_to_str(xdg: Path) -> None:
    out = save_templates([{"name": "n", "body": 123}])
    assert out == [{"name": "n", "body": "123"}]


def test_atomic_write_leaves_no_tmp(xdg: Path) -> None:
    save_templates([{"name": "foo", "body": "b"}])
    tpath = templates_path()
    assert tpath.exists()
    assert not tpath.with_suffix(".tmp").exists()
    assert oct(tpath.stat().st_mode)[-3:] == "600"


# --- RPC handlers --------------------------------------------------------


def test_rpc_load_when_missing(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "tui.load_templates", {})
    assert reply == {"ok": True, "templates": []}


def test_rpc_save_returns_normalized_and_persists(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "tui.save_templates",
        {"templates": [{"name": "b", "body": "2"}, {"name": "a", "body": "1"}]},
    )
    assert reply["ok"] is True
    assert reply["templates"] == [
        {"name": "a", "body": "1"},
        {"name": "b", "body": "2"},
    ]
    # Round-trips through load.
    load_reply = _call(host, "tui.load_templates", {})
    assert load_reply == {"ok": True, "templates": reply["templates"]}


def test_rpc_save_requires_list(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError):
        _call(host, "tui.save_templates", {"templates": "nope"})
