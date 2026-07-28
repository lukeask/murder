"""Built-in ``ticket`` workflow template registration + launch-oriented defaults."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from murder.user_config import (
    delete_workflow,
    put_workflow,
    read_workflow_registry,
    save_workflows,
)
from murder.work.workflows import (
    TICKET_WORKFLOW_NAME,
    StageDef,
    WorkflowDef,
    get_builtin_workflow,
    prepare_workflow_for_launch,
    ticket_workflow_template,
    validate_workflow,
)
from murder.work.workflows.builtins import apply_launch_execution, merge_builtin_workflows
from murder.work.workflows.launch import run_workflow_by_name
from murder.work.workflows.materialize import materialize_workflow


def test_ticket_builtin_registers_and_skips_harness_model_until_launch() -> None:
    defn = ticket_workflow_template()
    assert defn.name == TICKET_WORKFLOW_NAME
    assert defn.builtin is True
    assert defn.stages[0].title == "{title}"
    assert defn.stages[0].instructions == "{prompt}"
    assert defn.stages[0].harness is None
    assert defn.stages[0].model is None
    assert validate_workflow(defn) == []
    assert get_builtin_workflow("ticket") is not None
    assert get_builtin_workflow("ticket") is not defn  # fresh copy


def test_merge_builtin_workflows_overlays_ticket_and_wins_name_clash() -> None:
    merged = merge_builtin_workflows(
        [
            {
                "name": "ticket",
                "description": "user shadow",
                "stages": [
                    {"id": "x", "title": "X", "harness": "codex", "model": "gpt-5"},
                ],
            },
            {
                "name": "ship",
                "stages": [
                    {"id": "a", "title": "A", "harness": "codex", "model": "gpt-5"},
                ],
            },
        ]
    )
    names = [row["name"] for row in merged]
    assert names == ["ship", "ticket"]
    ticket = next(row for row in merged if row["name"] == "ticket")
    assert ticket["builtin"] is True
    assert ticket["description"] == "A single work item"


def test_registry_read_includes_builtin_ticket_without_persisting_it(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    snapshot = read_workflow_registry(path)
    assert any(row["name"] == "ticket" and row.get("builtin") for row in snapshot.workflows)
    assert not path.exists()

    saved = put_workflow(
        WorkflowDef(
            name="alpha",
            stages=[StageDef(id="a", title="A", harness="codex", model="gpt-5")],
        ).model_dump(mode="json"),
        original_name=None,
        expected_revision=snapshot.revision,
        path=path,
    )
    assert saved.ok
    assert [row["name"] for row in saved.workflows] == ["alpha", "ticket"]
    text = path.read_text(encoding="utf-8")
    assert "alpha" in text
    assert "ticket" not in text


def test_put_and_delete_reject_builtin_ticket_name(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    initial = read_workflow_registry(path)

    put = put_workflow(
        WorkflowDef(
            name="ticket",
            stages=[StageDef(id="a", title="A", harness="codex", model="gpt-5")],
        ).model_dump(mode="json"),
        original_name=None,
        expected_revision=initial.revision,
        path=path,
    )
    assert not put.ok
    assert put.issues[0]["code"] == "invalid_name"
    assert not path.exists()

    deleted = delete_workflow("ticket", expected_revision=initial.revision, path=path)
    assert not deleted.ok
    assert "built-in" in deleted.issues[0]["message"]


def test_save_workflows_strips_builtin_and_returns_merged_client_list(tmp_path: Path) -> None:
    path = tmp_path / "workflows.yaml"
    result = save_workflows(
        [
            ticket_workflow_template().model_dump(mode="json"),
            WorkflowDef(
                name="ship",
                stages=[StageDef(id="a", title="A", harness="codex", model="gpt-5")],
            ).model_dump(mode="json"),
        ],
        path=path,
    )
    assert [row["name"] for row in result] == ["ship", "ticket"]
    assert "ticket" not in path.read_text(encoding="utf-8")


def test_prepare_workflow_for_launch_fills_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "murder.work.workflows.builtins.configured_execution_defaults",
        lambda: ("codex", "gpt-5"),
    )
    resolved = prepare_workflow_for_launch(ticket_workflow_template(), {"title": "Fix it"})
    assert resolved.stages[0].harness == "codex"
    assert resolved.stages[0].model == "gpt-5"
    assert validate_workflow(resolved.model_copy(update={"builtin": False})) == []


def test_prepare_workflow_for_launch_prefers_arg_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "murder.work.workflows.builtins.configured_execution_defaults",
        lambda: ("codex", "gpt-5"),
    )
    resolved = apply_launch_execution(
        ticket_workflow_template(),
        {"harness": "claude_code", "model": "opus", "worktree": "feat"},
        default_harness="codex",
        default_model="gpt-5",
    )
    stage = resolved.stages[0]
    assert stage.harness == "claude_code"
    assert stage.model == "opus"
    assert stage.worktree == "feat"


def test_run_workflow_by_name_launches_builtin_ticket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "murder.work.workflows.builtins.configured_execution_defaults",
        lambda: ("codex", "gpt-5"),
    )
    captured: dict[str, object] = {}

    def fake_materialize(conn, repo_root, defn, args, **kwargs):  # noqa: ANN001
        captured["defn"] = defn
        captured["args"] = args
        return MagicMock(
            workflow_id="wid",
            run_ticket_id="t1",
            stage_ticket_ids={"work": "t2"},
            created_ticket_ids=["t1", "t2"],
        )

    monkeypatch.setattr(
        "murder.work.workflows.launch.materialize_workflow",
        fake_materialize,
    )
    result = run_workflow_by_name(
        MagicMock(),
        tmp_path,
        "ticket",
        {"title": "Ship it", "prompt": "do the thing"},
    )
    assert result.run_ticket_id == "t1"
    defn = captured["defn"]
    assert isinstance(defn, WorkflowDef)
    assert defn.name == "ticket"
    assert defn.stages[0].harness == "codex"
    assert defn.stages[0].model == "gpt-5"
    assert captured["args"] == {"title": "Ship it", "prompt": "do the thing"}


def test_run_workflow_by_name_rejects_ticket_without_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "murder.work.workflows.builtins.configured_execution_defaults",
        lambda: (None, None),
    )
    with pytest.raises(ValueError, match="requires a harness"):
        run_workflow_by_name(MagicMock(), tmp_path, "ticket", {"title": "x"})


def test_materialize_prepared_ticket_creates_workflow_run(repo_root: Path) -> None:
    from murder.state.persistence.schema import get_db, init_db
    from murder.state.persistence.workflow_runs import get_workflow_run
    from murder.state.storage.paths import ticket_md
    from murder.work.tickets.parser import parse_ticket

    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)

    resolved = apply_launch_execution(
        ticket_workflow_template(),
        {"title": "Fix login", "prompt": "check auth"},
        default_harness="codex",
        default_model="gpt-5",
    )
    result = materialize_workflow(
        conn, repo_root, resolved, {"title": "Fix login", "prompt": "check auth"}
    )

    assert result.stage_ticket_ids == {"work": result.created_ticket_ids[1]}
    run = get_workflow_run(conn, result.workflow_id)
    assert run is not None
    assert run.definition_name == "ticket"
    assert run.stage_map == result.stage_ticket_ids

    stage_path = ticket_md(repo_root, result.stage_ticket_ids["work"])
    parsed = parse_ticket(stage_path.read_text(encoding="utf-8"))
    assert parsed.title == "Fix login"
    assert "check auth" in parsed.body
    assert parsed.harness == "codex"
    assert parsed.model == "gpt-5"
