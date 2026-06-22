"""Pure-model tests for the workflow definition + ``validate_workflow``.

No I/O: these drive the validator and pydantic round-trip directly. The validator
is the contract the storage normalizer (``save_workflows``) leans on to decide
which definitions are safe to persist, so its rejection cases are exercised one
invariant at a time.
"""

from __future__ import annotations

from murder.work.workflows import StageDef, WorkflowDef, validate_workflow


def _stage(**kw) -> StageDef:
    # Stages require harness+model (every stage is a frontmatter ticket); fill
    # sensible defaults so each test isolates the invariant under examination.
    kw.setdefault("harness", "codex")
    kw.setdefault("model", "gpt-5")
    return StageDef(**kw)


def _wf(**kw) -> WorkflowDef:
    kw.setdefault("name", "wf")
    kw.setdefault("stages", [_stage(id="a", title="A")])
    return WorkflowDef(**kw)


def test_valid_workflow_passes() -> None:
    defn = WorkflowDef(
        name="ship_it",
        stages=[
            _stage(id="plan", title="Plan"),
            _stage(id="build", title="Build", depends_on=["plan"]),
            _stage(id="review", title="Review", depends_on=["build"]),
        ],
    )
    assert validate_workflow(defn) == []


def test_stage_requires_harness_and_model() -> None:
    defn = WorkflowDef(name="wf", stages=[StageDef(id="a", title="A")])
    errors = validate_workflow(defn)
    assert any("requires a harness" in e for e in errors)
    assert any("requires a model" in e for e in errors)


def test_missing_name() -> None:
    errors = validate_workflow(_wf(name=""))
    assert any("name" in e for e in errors)


def test_bad_name_charset() -> None:
    errors = validate_workflow(_wf(name="bad name"))
    assert any("name" in e for e in errors)


def test_empty_stages() -> None:
    errors = validate_workflow(WorkflowDef(name="wf", stages=[]))
    assert any("no stages" in e for e in errors)


def test_duplicate_stage_ids() -> None:
    defn = WorkflowDef(
        name="wf",
        stages=[StageDef(id="a", title="A"), StageDef(id="a", title="A2")],
    )
    errors = validate_workflow(defn)
    assert any("duplicate stage id 'a'" in e for e in errors)


def test_bad_stage_id_charset() -> None:
    defn = WorkflowDef(name="wf", stages=[StageDef(id="bad id", title="X")])
    errors = validate_workflow(defn)
    assert any("stage id" in e for e in errors)


def test_dangling_depends_on() -> None:
    defn = WorkflowDef(
        name="wf",
        stages=[StageDef(id="a", title="A", depends_on=["ghost"])],
    )
    errors = validate_workflow(defn)
    assert any("unknown stage 'ghost'" in e for e in errors)


def test_duplicate_depends_on() -> None:
    # A dep listed twice double-counts in the Kahn indegree and duplicates the
    # dep id in the stage frontmatter; reject it as malformed input.
    defn = WorkflowDef(
        name="wf",
        stages=[
            _stage(id="a", title="A"),
            _stage(id="b", title="B", depends_on=["a", "a"]),
        ],
    )
    errors = validate_workflow(defn)
    assert any("duplicate dependency 'a'" in e for e in errors)


def test_self_dependency() -> None:
    defn = WorkflowDef(
        name="wf",
        stages=[StageDef(id="a", title="A", depends_on=["a"])],
    )
    errors = validate_workflow(defn)
    assert any("depends on itself" in e for e in errors)


def test_cycle_detected() -> None:
    # a -> b -> a : no root, and a true cycle. Assert the cycle is reported.
    defn = WorkflowDef(
        name="wf",
        stages=[
            StageDef(id="a", title="A", depends_on=["b"]),
            StageDef(id="b", title="B", depends_on=["a"]),
        ],
    )
    errors = validate_workflow(defn)
    assert any("cycle" in e for e in errors)


def test_static_with_no_root() -> None:
    # Every stage depends on another but acyclic-ness is irrelevant: with no
    # zero-dependency stage there is nothing to launch first.
    defn = WorkflowDef(
        name="wf",
        mode="static",
        stages=[
            StageDef(id="a", title="A", depends_on=["b"]),
            StageDef(id="b", title="B", depends_on=["c"]),
            StageDef(id="c", title="C", depends_on=["a"]),
        ],
    )
    errors = validate_workflow(defn)
    assert any("no root stage" in e for e in errors)


def test_model_round_trips() -> None:
    defn = WorkflowDef(
        name="wf",
        description="desc",
        stages=[
            StageDef(id="a", title="A", instructions="do {thing}", harness="codex"),
            StageDef(id="b", title="B", depends_on=["a"], gate="human"),
        ],
    )
    dumped = defn.model_dump(mode="json")
    assert WorkflowDef.model_validate(dumped) == defn


def test_defaults_applied_on_validate() -> None:
    defn = WorkflowDef.model_validate(
        {"name": "wf", "stages": [{"id": "a", "title": "A"}]}
    )
    assert defn.mode == "static"
    assert defn.stages[0].gate == "auto"
    assert defn.stages[0].depends_on == []
