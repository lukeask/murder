"""Unit tests for server-authoritative workflow-template compilation."""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.app.protocol.requests import QueryName
from murder.app.service.handlers import workflows as workflows_handlers
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.workflow_runs import get_workflow_run
from murder.state.storage.paths import ticket_md
from murder.work.tickets.parser import parse_ticket
from murder.work.workflows.compile import (
    apply_input_defaults,
    collect_placeholders,
    compile_workflow_template,
    expand_inline_prompt_templates,
    required_input_issues,
)
from murder.work.workflows.definition import StageDef, WorkflowDef, WorkflowInputDecl
from murder.work.workflows.launch import start_workflow_from_def
from murder.work.workflows.runtime import StaticDagWorkflowStateV1


def _stage(**kw: object) -> StageDef:
    kw.setdefault("harness", "codex")
    kw.setdefault("model", "gpt-5")
    return StageDef(**kw)  # type: ignore[arg-type]


def _conn(repo_root: Path) -> sqlite3.Connection:
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


def test_expand_inline_prompt_templates_single_pass() -> None:
    templates = {
        "review-context": "Review {subject}.\nCheck :nested:",
        "nested": "should-not-expand",
    }
    expanded, unknown = expand_inline_prompt_templates(
        "Intro\n:review-context:\nOutro",
        templates,
    )
    assert unknown == []
    assert expanded == "Intro\nReview {subject}.\nCheck :nested:\nOutro"


def test_expand_unknown_prompt_template_left_literal() -> None:
    expanded, unknown = expand_inline_prompt_templates("see :missing: please", {})
    assert expanded == "see :missing: please"
    assert unknown == ["missing"]


def test_collect_placeholders_dedupes_first_occurrence_order() -> None:
    assert collect_placeholders("A {b} {a}", "{a} then {c} and {b}") == ["b", "a", "c"]


def test_compile_expands_templates_and_infers_inputs() -> None:
    defn = WorkflowDef(
        name="review",
        stages=[
            _stage(
                id="review",
                title="Review {subject}",
                instructions=":review-context:\nReturn a report.",
            )
        ],
    )
    result = compile_workflow_template(
        defn,
        prompt_templates={
            "review-context": "Review {subject}.\nCheck specifically for {risk_area}."
        },
    )

    assert result.ok
    assert result.issues == []
    stage = result.expanded_template.stages[0]
    assert stage.title == "Review {subject}"
    assert (
        stage.instructions
        == "Review {subject}.\nCheck specifically for {risk_area}.\nReturn a report."
    )
    assert [field.name for field in result.inputs] == ["subject", "risk_area"]
    assert all(field.inferred for field in result.inputs)
    assert all(field.kind == "text" for field in result.inputs)
    assert ":review-context:" not in stage.instructions
    assert "{subject}" in stage.instructions


def test_compile_unknown_prompt_template_is_error() -> None:
    defn = WorkflowDef(
        name="broken",
        stages=[_stage(id="a", title="A", instructions=":no-such:")],
    )
    result = compile_workflow_template(defn, prompt_templates={})
    assert not result.ok
    assert [(issue.code, issue.severity, issue.template_name) for issue in result.issues] == [
        ("unknown_prompt_template", "error", "no-such")
    ]
    assert result.expanded_template.stages[0].instructions == ":no-such:"


def test_compile_declared_inputs_refine_and_order() -> None:
    defn = WorkflowDef(
        name="review",
        inputs={
            "risk_area": WorkflowInputDecl(
                label="Particular risk area",
                kind="text",
                default="correctness",
            ),
            "subject": WorkflowInputDecl(
                label="What should be reviewed?",
                kind="multiline",
                required=True,
            ),
            "unused": WorkflowInputDecl(label="Unused"),
        },
        stages=[
            _stage(
                id="review",
                title="Review {subject}",
                instructions="Focus on {risk_area}. Also note {extra}.",
            )
        ],
    )
    result = compile_workflow_template(defn, prompt_templates={})

    assert result.ok
    assert [field.name for field in result.inputs] == [
        "risk_area",
        "subject",
        "unused",
        "extra",
    ]
    by_name = {field.name: field for field in result.inputs}
    assert by_name["subject"].kind == "multiline"
    assert by_name["subject"].required is True
    assert by_name["subject"].inferred is False
    assert by_name["risk_area"].default == "correctness"
    assert by_name["extra"].inferred is True
    assert by_name["extra"].required is False
    assert [(issue.code, issue.input_name, issue.severity) for issue in result.issues] == [
        ("unused_input", "unused", "warning")
    ]


def test_repeated_placeholders_produce_one_field() -> None:
    defn = WorkflowDef(
        name="dup",
        stages=[
            _stage(id="a", title="{topic}", instructions="Again {topic} and {topic}"),
            _stage(id="b", title="{topic}", instructions=""),
        ],
    )
    result = compile_workflow_template(defn, prompt_templates={})
    assert [field.name for field in result.inputs] == ["topic"]


def test_required_input_issues_and_defaults() -> None:
    defn = WorkflowDef(
        name="req",
        inputs={
            "subject": WorkflowInputDecl(required=True, kind="multiline"),
            "risk_area": WorkflowInputDecl(default="correctness"),
        },
        stages=[
            _stage(
                id="a",
                title="{subject}",
                instructions="{risk_area}",
            )
        ],
    )
    compiled = compile_workflow_template(defn, prompt_templates={})
    merged = apply_input_defaults(compiled.inputs, {})
    assert merged["risk_area"] == "correctness"
    assert "subject" not in merged
    missing = required_input_issues(compiled.inputs, merged)
    assert [issue.input_name for issue in missing] == ["subject"]

    filled = apply_input_defaults(compiled.inputs, {"subject": "auth rewrite"})
    assert required_input_issues(compiled.inputs, filled) == []
    assert filled["risk_area"] == "correctness"


def test_start_rejects_unknown_template_and_required_gap(repo_root: Path) -> None:
    conn = _conn(repo_root)
    broken = WorkflowDef(
        name="broken",
        stages=[_stage(id="a", title="A", instructions=":missing:")],
    )
    with pytest.raises(ValueError, match="unknown prompt template"):
        start_workflow_from_def(conn, repo_root, broken, {}, prompt_templates={})

    required = WorkflowDef(
        name="required",
        inputs={"subject": WorkflowInputDecl(required=True)},
        stages=[_stage(id="a", title="{subject}", instructions="go")],
    )
    with pytest.raises(ValueError, match="required input"):
        start_workflow_from_def(conn, repo_root, required, {}, prompt_templates={})


def test_start_snapshots_expanded_templates_with_unresolved_inputs(
    repo_root: Path,
) -> None:
    conn = _conn(repo_root)
    defn = WorkflowDef(
        name="review",
        inputs={
            "subject": WorkflowInputDecl(required=True, kind="multiline"),
            "risk_area": WorkflowInputDecl(default="correctness"),
        },
        stages=[
            _stage(
                id="review",
                title="Review {subject}",
                instructions=":review-context:\nDone.",
                worktree="shared",
            )
        ],
    )
    result = start_workflow_from_def(
        conn,
        repo_root,
        defn,
        {"subject": "billing"},
        prompt_templates={
            "review-context": "Review {subject}. Focus on {risk_area}."
        },
    )

    run = get_workflow_run(conn, result.workflow_id)
    assert run is not None
    snapshot = run.definition_snapshot
    assert snapshot is not None
    stage = snapshot["stages"][0]
    assert stage["title"] == "Review {subject}"
    assert stage["instructions"] == "Review {subject}. Focus on {risk_area}.\nDone."
    assert ":review-context:" not in stage["instructions"]

    ticket_id = result.stage_ticket_ids["review"]
    md = ticket_md(repo_root, ticket_id).read_text(encoding="utf-8")
    parsed = parse_ticket(md, default_title=ticket_id)
    assert parsed.parse_error is None
    assert "Review billing. Focus on correctness." in parsed.body
    assert parsed.title == "Review billing"


def test_post_start_run_immune_to_template_edits(repo_root: Path) -> None:
    """After start, editing prompt/workflow templates must not change the run."""
    conn = _conn(repo_root)
    defn = WorkflowDef(
        name="review",
        stages=[
            _stage(
                id="review",
                title="Review {subject}",
                instructions=":review-context:\nShip it.",
                worktree="shared",
            )
        ],
    )
    prompt_templates = {
        "review-context": "Review {subject}. Focus on {risk_area}."
    }
    result = start_workflow_from_def(
        conn,
        repo_root,
        defn,
        {"subject": "billing", "risk_area": "correctness"},
        prompt_templates=prompt_templates,
    )

    run = get_workflow_run(conn, result.workflow_id)
    assert run is not None
    snapshot_before = copy.deepcopy(run.definition_snapshot)
    state_before = StaticDagWorkflowStateV1.model_validate(run.state.value)
    inputs_before = dict(state_before.inputs)
    ticket_bodies_before = {
        stage_id: ticket_md(repo_root, ticket_id).read_text(encoding="utf-8")
        for stage_id, ticket_id in result.stage_ticket_ids.items()
    }
    parent_before = ticket_md(repo_root, result.run_ticket_id).read_text(encoding="utf-8")

    # Mutate the prompt template and the workflow definition that would be used
    # for a *future* start — already-materialized runs must stay frozen.
    prompt_templates["review-context"] = "CHANGED TEMPLATE {subject}"
    defn.stages[0].instructions = ":review-context:\nDIFFERENT OUTCOME."
    defn.stages[0].title = "Mutated {subject}"

    run_after = get_workflow_run(conn, result.workflow_id)
    assert run_after is not None
    assert run_after.definition_snapshot == snapshot_before
    state_after = StaticDagWorkflowStateV1.model_validate(run_after.state.value)
    assert dict(state_after.inputs) == inputs_before
    assert inputs_before == {"subject": "billing", "risk_area": "correctness"}
    for stage_id, ticket_id in result.stage_ticket_ids.items():
        assert (
            ticket_md(repo_root, ticket_id).read_text(encoding="utf-8")
            == ticket_bodies_before[stage_id]
        )
    assert ticket_md(repo_root, result.run_ticket_id).read_text(encoding="utf-8") == parent_before
    stage = snapshot_before["stages"][0]
    assert stage["instructions"] == "Review {subject}. Focus on {risk_area}.\nShip it."
    assert "CHANGED" not in stage["instructions"]


class _CompileHost:
    def __init__(self) -> None:
        self.queries: dict[QueryName, object] = {}
        self.commands: dict[object, object] = {}

    def register_application_query(self, name: QueryName, handler: object) -> None:
        self.queries[name] = handler

    def register_application_command(self, name: object, handler: object) -> None:
        self.commands[name] = handler


def test_workflow_compile_handler_resolves_builtin_ticket_by_name() -> None:
    host = _CompileHost()
    workflows_handlers.register(
        host,  # type: ignore[arg-type]
        ProjectionProviderRegistry(),
        SimpleNamespace(db=None),
    )
    compile_handler = host.queries[QueryName.WORKFLOW_COMPILE]
    assert callable(compile_handler)

    result = compile_handler({"name": "ticket"})
    assert result["ok"] is True
    assert result["expanded_template"]["name"] == "ticket"
    assert result["expanded_template"]["builtin"] is True
    by_name = {field["name"]: field for field in result["inputs"]}
    assert by_name["title"]["label"] == "Title"
    assert by_name["title"]["kind"] == "text"
    assert by_name["title"]["required"] is True
    assert by_name["title"]["inferred"] is False
    assert by_name["prompt"]["label"] == "Instructions"
    assert by_name["prompt"]["kind"] == "multiline"
    assert by_name["prompt"]["required"] is False
    assert by_name["prompt"]["inferred"] is False
    stage = result["expanded_template"]["stages"][0]
    assert stage["title"] == "{title}"
    assert stage["instructions"] == "{prompt}"


def test_workflow_compile_handler_inline_template() -> None:
    host = _CompileHost()
    workflows_handlers.register(
        host,  # type: ignore[arg-type]
        ProjectionProviderRegistry(),
        SimpleNamespace(db=None),
    )
    compile_handler = host.queries[QueryName.WORKFLOW_COMPILE]
    assert callable(compile_handler)

    result = compile_handler(
        {
            "template": {
                "name": "draft",
                "stages": [
                    {
                        "id": "a",
                        "title": "T {x}",
                        "instructions": ":ctx:\nend",
                        "harness": "codex",
                        "model": "gpt-5",
                    }
                ],
            },
            "prompt_templates": {"ctx": "Body {y}"},
        }
    )
    assert result["ok"] is True
    assert result["expanded_template"]["stages"][0]["instructions"] == "Body {y}\nend"
    assert [field["name"] for field in result["inputs"]] == ["x", "y"]
