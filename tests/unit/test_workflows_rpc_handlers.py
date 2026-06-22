"""Userspace workflow registry: user_config helpers + host RPC handlers.

The ``tui.{load,save}_workflows`` handlers persist a global ``workflows.yaml`` under
the XDG config home (NOT the per-repo prefs file). We point ``XDG_CONFIG_HOME`` at a
tmp dir so each test is isolated, and assert the normalization/atomic-write contract
the frontend is built against — mirroring the templates registry tests.
"""

from __future__ import annotations

import asyncio
import inspect
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
    load_workflows,
    save_workflows,
    workflows_path,
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
    result = host._rpc_handlers[method](body)
    if inspect.iscoroutine(result):
        # Async handlers (e.g. tui.run_workflow) return a coroutine; drive it so
        # callers always see the resolved reply instead of a stray awaitable.
        return asyncio.run(result)  # type: ignore[return-value]
    return result  # type: ignore[return-value]


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


def _wf(name: str, *stage_ids: str) -> dict:
    """A minimal valid workflow dict (single root stage when no ids given)."""
    ids = stage_ids or ("root",)
    return {
        "name": name,
        # Stages require harness+model to survive validation/normalization.
        "stages": [
            {"id": sid, "title": sid.title(), "harness": "codex", "model": "gpt-5"}
            for sid in ids
        ],
    }


# --- user_config helpers -------------------------------------------------


def test_load_missing_returns_empty(xdg: Path) -> None:
    assert not workflows_path().exists()
    assert load_workflows() == []


def test_save_then_load_round_trips(xdg: Path) -> None:
    out = save_workflows([_wf("foo")])
    assert load_workflows() == out
    assert out[0]["name"] == "foo"
    # Canonical dump carries the model defaults (mode, gate, etc.).
    assert out[0]["mode"] == "static"
    assert out[0]["stages"][0]["gate"] == "auto"


def test_normalization_invalid_dropped_dupes_collapsed_sorted(xdg: Path) -> None:
    out = save_workflows(
        [
            _wf("zed"),
            {"name": "bad name", "stages": [{"id": "a", "title": "A"}]},  # bad name -> dropped
            {"name": "empty", "stages": []},  # no stages -> dropped
            {"name": "cyclic", "stages": [  # cycle, no root -> dropped
                {"id": "a", "title": "A", "depends_on": ["b"]},
                {"id": "b", "title": "B", "depends_on": ["a"]},
            ]},
            {**_wf("dup"), "description": "first"},
            {**_wf("dup"), "description": "last"},  # last wins
            _wf("abc"),
            "not a dict",  # ignored
        ]
    )
    assert [w["name"] for w in out] == ["abc", "dup", "zed"]
    dup = next(w for w in out if w["name"] == "dup")
    assert dup["description"] == "last"
    # Return value is the canonical persisted state.
    assert load_workflows() == out


def test_atomic_write_leaves_no_tmp(xdg: Path) -> None:
    save_workflows([_wf("foo")])
    wpath = workflows_path()
    assert wpath.exists()
    assert not wpath.with_suffix(".tmp").exists()
    assert oct(wpath.stat().st_mode)[-3:] == "600"


# --- RPC handlers --------------------------------------------------------


def test_rpc_load_when_missing(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(host, "tui.load_workflows", {})
    assert reply == {"ok": True, "workflows": []}


def test_rpc_save_returns_normalized_and_persists(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    reply = _call(
        host,
        "tui.save_workflows",
        {"workflows": [_wf("b"), _wf("a")]},
    )
    assert reply["ok"] is True
    assert [w["name"] for w in reply["workflows"]] == ["a", "b"]
    # Round-trips through load.
    load_reply = _call(host, "tui.load_workflows", {})
    assert load_reply == {"ok": True, "workflows": reply["workflows"]}


def test_rpc_save_requires_list(repo_root: Path, xdg: Path) -> None:
    host = _host(repo_root)
    with pytest.raises(ValueError):
        _call(host, "tui.save_workflows", {"workflows": "nope"})


def test_rpc_run_workflow_pre_start_reports_not_started(repo_root: Path, xdg: Path) -> None:
    # A never-started host has no runtime/orchestrator; the handler must surface
    # the shared "service not started" error rather than an internal
    # "orchestrator unavailable" leak.
    host = _host(repo_root)
    with pytest.raises(RuntimeError, match="service not started"):
        _call(host, "tui.run_workflow", {"name": "anything"})
