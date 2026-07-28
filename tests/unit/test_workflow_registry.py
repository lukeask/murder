"""Atomic optimistic-concurrency workflow registry behavior."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from murder import user_config
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.handlers import tui
from murder.user_config import (
    delete_workflow,
    put_workflow,
    read_workflow_registry,
)
from murder.work.workflows import StageDef, WorkflowDef, launch


def _workflow(name: str, **kwargs: object) -> dict[str, object]:
    stages = kwargs.pop(
        "stages",
        [StageDef(id="build", title="Build", harness="codex", model="gpt-5")],
    )
    workflow = WorkflowDef(
        name=name,
        stages=stages,  # type: ignore[arg-type]
        **kwargs,
    )
    return workflow.model_dump(mode="json")


def test_put_invalid_definition_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    snapshot = read_workflow_registry(path)

    result = put_workflow(
        _workflow("broken", stages=[]),
        original_name=None,
        expected_revision=snapshot.revision,
        path=path,
    )

    assert not result.ok
    assert not result.conflict
    assert [issue["code"] for issue in result.issues] == ["no_stages"]
    assert result.revision == snapshot.revision
    assert not path.exists()


def test_put_rename_replaces_only_original_and_rejects_collisions(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    first = read_workflow_registry(path)
    saved = put_workflow(
        _workflow("alpha"), original_name=None, expected_revision=first.revision, path=path
    )
    second = put_workflow(
        _workflow("beta"), original_name=None, expected_revision=saved.revision, path=path
    )

    collision = put_workflow(
        _workflow("beta"), original_name="alpha", expected_revision=second.revision, path=path
    )
    assert not collision.ok
    assert collision.issues[0]["code"] == "invalid_name"
    assert path.read_text(encoding="utf-8") == (
        "workflows:\n"
        "- name: alpha\n"
        "  definition_version: 1\n"
        "  description: ''\n"
        "  mode: static\n"
        "  stages:\n"
        "  - id: build\n"
        "    title: Build\n"
        "    instructions: ''\n"
        "    harness: codex\n"
        "    model: gpt-5\n"
        "    worktree: null\n"
        "    depends_on: []\n"
        "    gate: auto\n"
        "- name: beta\n"
        "  definition_version: 1\n"
        "  description: ''\n"
        "  mode: static\n"
        "  stages:\n"
        "  - id: build\n"
        "    title: Build\n"
        "    instructions: ''\n"
        "    harness: codex\n"
        "    model: gpt-5\n"
        "    worktree: null\n"
        "    depends_on: []\n"
        "    gate: auto\n"
    )

    renamed = put_workflow(
        _workflow("release"), original_name="alpha", expected_revision=second.revision, path=path
    )
    assert renamed.ok
    assert [workflow["name"] for workflow in renamed.workflows] == ["beta", "release"]

    updated = put_workflow(
        _workflow("beta", description="updated"),
        original_name=None,
        expected_revision=renamed.revision,
        path=path,
    )
    assert updated.ok
    assert (
        next(workflow for workflow in updated.workflows if workflow["name"] == "beta")[
            "description"
        ]
        == "updated"
    )


def test_stale_put_and_delete_do_not_write(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    initial = read_workflow_registry(path)
    saved = put_workflow(
        _workflow("alpha"), original_name=None, expected_revision=initial.revision, path=path
    )
    before = path.read_bytes()

    stale_put = put_workflow(
        _workflow("beta"), original_name=None, expected_revision=initial.revision, path=path
    )
    stale_delete = delete_workflow("alpha", expected_revision=initial.revision, path=path)

    assert stale_put.conflict and not stale_put.ok
    assert stale_delete.conflict and not stale_delete.ok
    assert stale_put.revision == stale_delete.revision == saved.revision
    assert path.read_bytes() == before


def test_concurrent_writers_with_one_revision_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    initial = read_workflow_registry(path)

    def put(name: str):  # type: ignore[no-untyped-def]
        return put_workflow(
            _workflow(name), original_name=None, expected_revision=initial.revision, path=path
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(put, ("alpha", "beta")))

    assert sum(result.ok for result in results) == 1
    assert sum(result.conflict for result in results) == 1
    assert len(read_workflow_registry(path).workflows) == 1


def test_reserved_values_are_persisted_with_runtime_warnings(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    initial = read_workflow_registry(path)
    result = put_workflow(
        WorkflowDef(
            name="reserved",
            mode="generative",
            stages=[
                StageDef(id="build", title="Build", harness="codex", model="gpt-5", gate="human")
            ],
        ).model_dump(mode="json"),
        original_name=None,
        expected_revision=initial.revision,
        path=path,
    )

    assert result.ok
    assert {issue["code"] for issue in result.issues} == {"unsupported_mode", "unsupported_gate"}
    assert {issue["severity"] for issue in result.issues} == {"warning"}


class _Host:
    def __init__(self) -> None:
        self.repo_root = Path("/repo")
        self.runtime: object | None = None
        self.orchestrator: object | None = None
        self.queries: dict[QueryName, object] = {}
        self.commands: dict[CommandName, object] = {}

    def register_application_query(self, name: QueryName, handler: object) -> None:
        self.queries[name] = handler

    def register_application_command(self, name: CommandName, handler: object) -> None:
        self.commands[name] = handler


def test_tui_handlers_publish_revisions_and_workflow_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(user_config, "workflows_path", lambda: tmp_path / "workflows.yaml")
    host = _Host()
    tui.register(host)  # type: ignore[arg-type]

    get = host.queries[QueryName.WORKFLOWS_GET]
    assert callable(get)
    initial = get({})
    assert initial["revision"]

    put = host.commands[CommandName.WORKFLOW_PUT]
    assert callable(put)
    saved = put({"workflow": _workflow("alpha"), "expected_revision": initial["revision"]})
    assert saved["ok"]
    assert saved["workflow"]["name"] == "alpha"

    workflow_id = uuid4()

    class _Orchestrator:
        async def kickoff_ready(self, *, only: str) -> None:
            assert only == "t2"

    def run_workflow(*_args: object) -> object:
        return SimpleNamespace(
            workflow_id=workflow_id,
            run_ticket_id="t1",
            stage_ticket_ids={"build": "t2"},
            created_ticket_ids=["t1", "t2"],
        )

    monkeypatch.setattr(launch, "run_workflow_by_name", run_workflow)
    host.runtime = SimpleNamespace(db=object())
    host.orchestrator = _Orchestrator()
    start = host.commands[CommandName.WORKFLOW_START]
    assert callable(start)
    result = start({"name": "alpha"})
    assert hasattr(result, "__await__")
    assert asyncio.run(result)["workflow_id"] == workflow_id
