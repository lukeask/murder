from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from murder.bus import OrchestrationNotifier
from murder.llm.harness_control.capabilities.permissions import permission_fingerprint
from murder.llm.harness_control.capabilities.questions import question_fingerprint
from murder.llm.harness_control.model import (
    ChoiceState,
    HarnessId,
    Knowledge,
    ObservationRevision,
    Observed,
    PermissionRequestState,
    QuestionState,
    unknown_snapshot,
)
from murder.permissions import PermissionPrincipal, PermissionStore
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.state.persistence.schema import get_db, init_db

EXPECTED_QUESTION_EXECUTIONS = 3


@pytest.mark.asyncio
async def test_structured_decisions_are_durable_identity_bound_and_terminal_free(  # noqa: PLR0915
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every decision kind obeys the same publish-once and exact-binding law."""

    monkeypatch.setattr(
        "murder.runtime.terminal.tmux.send_keys",
        AsyncMock(side_effect=AssertionError("the decision router emitted terminal input")),
    )
    db = get_db(tmp_path / "murder.db")
    init_db(db)
    db.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-1", "2026-07-12T00:00:00+00:00", "{}"),
    )
    bus = OrchestrationNotifier("run-1", db)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    revision = ObservationRevision(1, 2, 3)
    base = unknown_snapshot(HarnessId("codex"), captured_at=now, revision=revision)
    absent_base = replace(
        base,
        question=Observed.without_value(
            Knowledge.ABSENT, evidence=(), observed_at=now, revision=revision
        ),
        permission_request=Observed.without_value(
            Knowledge.ABSENT, evidence=(), observed_at=now, revision=revision
        ),
    )
    question = QuestionState(
        "q-1",
        "Choose a deploy target",
        (ChoiceState("blue", "Blue", disabled=False, highlighted=True),),
        "single_select",
        None,
        (),
        False,
        None,
        "Submit",
        "Cancel",
        (),
    )
    permission = PermissionRequestState(
        "p-1",
        "shell",
        "deploy blue",
        "Deploy the blue target",
        (
            ChoiceState("allow_once", "Allow once", disabled=False, highlighted=True),
            ChoiceState("deny", "Deny", disabled=False),
        ),
        "allow_once",
        frozenset({"shell", "network"}),
    )
    snapshots = {
        "question": replace(
            base,
            question=Observed.present(question, evidence=(), observed_at=now, revision=revision),
        ),
        "permission": replace(
            base,
            permission_request=Observed.present(
                permission, evidence=(), observed_at=now, revision=revision
            ),
        ),
    }
    agent = SimpleNamespace(
        id="codex-agent",
        latest_ingested_frame=None,
        answer_verified_question=AsyncMock(return_value=True),
        answer_verified_permission=AsyncMock(return_value=True),
    )
    runtime = SimpleNamespace(
        db=db,
        bus=bus,
        run_id="run-1",
        get_agent=lambda agent_id: agent if agent_id == agent.id else None,
    )
    router = StructuredDecisionRouter(runtime)

    for kind, snapshot in snapshots.items():
        agent.latest_ingested_frame = SimpleNamespace(snapshot=snapshot)
        await router.observe(agent, snapshot)
        await router.observe(agent, snapshot)

        rows = db.execute(
            "SELECT decision_request_id, decision_kind, request_identity, request_json "
            "FROM structured_decisions WHERE agent_id = ?",
            (agent.id,),
        ).fetchall()
        matching = [
            {
                "decision_request_id": row["decision_request_id"],
                "decision_kind": row["decision_kind"],
                "request_identity": row["request_identity"],
                "request": json.loads(row["request_json"]),
            }
            for row in rows
            if row["decision_kind"] == kind
        ]
        assert len(matching) == 1
        request = matching[0]
        identity = (
            question_fingerprint(question)
            if kind == "question"
            else permission_fingerprint(permission)
        )
        assert request["request_identity"] == identity

        # Publish-once is durable across router reconstruction, not merely an
        # in-memory debounce within one service lifetime.
        await StructuredDecisionRouter(runtime).observe(agent, snapshot)
        assert (
            db.execute(
                "SELECT count(*) FROM structured_decisions "
                "WHERE agent_id = ? AND decision_request_id = ?",
                (agent.id, request["decision_request_id"]),
            ).fetchone()[0]
            == 1
        )

        stale = await router.respond(
            {
                "agent_id": agent.id,
                "decision_kind": kind,
                "decision_request_id": request["decision_request_id"],
                "request_identity": f"stale-{identity}",
                "response": {"mode": "single", "selections": [{"id": "blue", "label": "Blue"}]}
                if kind == "question"
                else {
                    "kind": "allow_once",
                    "id": "allow_once",
                    "label": "Allow once",
                },
                "decided_by": "test-user",
            }
        )
        assert stale == {"ok": False, "error": "request_identity_mismatch"}

        agent.latest_ingested_frame = SimpleNamespace(snapshot=base)
        stale_observation = await router.respond(
            {
                "agent_id": agent.id,
                "decision_kind": kind,
                "decision_request_id": request["decision_request_id"],
                "request_identity": identity,
                "response": {"mode": "single", "selections": [{"id": "blue", "label": "Blue"}]}
                if kind == "question"
                else {
                    "kind": "allow_once",
                    "id": "allow_once",
                    "label": "Allow once",
                },
                "decided_by": "test-user",
            }
        )
        assert stale_observation == {"ok": False, "error": "request_not_current"}
        agent.latest_ingested_frame = SimpleNamespace(snapshot=snapshot)

        accepted = await router.respond(
            {
                "agent_id": agent.id,
                "decision_kind": kind,
                "decision_request_id": request["decision_request_id"],
                "request_identity": identity,
                "response": {"mode": "single", "selections": [{"id": "blue", "label": "Blue"}]}
                if kind == "question"
                else {
                    "kind": "allow_once",
                    "id": "allow_once",
                    "label": "Allow once",
                },
                "decided_by": "test-user",
            }
        )
        assert accepted == {"ok": True}
        db.execute(
            """
            INSERT INTO harness_control_operations(
                operation_id, harness_id, session_id, capability, status, phase_type,
                phase_payload_json, request_json, operation_state_json, created_at,
                updated_at, deadline, attempt_count, warnings_json
            ) VALUES (?, 'codex', 'codex-agent', ?, 'SUCCEEDED', 'test', '{}', '{}', '{}',
                      ?, ?, NULL, 0, '[]')
            """,
            (request["decision_request_id"], kind, now.isoformat(), now.isoformat()),
        )
        later_revision = ObservationRevision(1, revision.capture_sequence + 10, 4)
        repeated_occurrence = replace(snapshot, revision=later_revision)
        restarted_router = StructuredDecisionRouter(runtime)
        await restarted_router.observe(agent, absent_base)
        agent.latest_ingested_frame = SimpleNamespace(snapshot=repeated_occurrence)
        await restarted_router.observe(agent, repeated_occurrence)
        assert (
            db.execute(
                "SELECT count(*) FROM structured_decisions "
                "WHERE agent_id = ? AND decision_kind = ? AND request_identity = ?",
                (agent.id, kind, identity),
            ).fetchone()[0]
            == 2  # noqa: PLR2004 - two distinct occurrences of identical content
        )

    crash_question = replace(question, question_id_hint="q-crash", prompt_text="Crash-safe choice")
    crash_snapshot = replace(
        base,
        question=Observed.present(
            crash_question, evidence=(), observed_at=now, revision=revision
        ),
    )
    agent.latest_ingested_frame = SimpleNamespace(snapshot=crash_snapshot)
    await router.observe(agent, crash_snapshot)
    crash_identity = question_fingerprint(crash_question)
    crash_row = db.execute(
        "SELECT decision_request_id, decision_kind, request_identity, request_json "
        "FROM structured_decisions WHERE request_identity = ?",
        (crash_identity,),
    ).fetchone()
    assert crash_row is not None
    crash_request = {
        "decision_request_id": crash_row["decision_request_id"],
        "decision_kind": crash_row["decision_kind"],
        "request_identity": crash_row["request_identity"],
        "request": json.loads(crash_row["request_json"]),
    }
    agent.answer_verified_question.side_effect = RuntimeError("crash before operation persist")
    with pytest.raises(RuntimeError, match="crash before operation"):
        await router.respond(
            {
                "agent_id": agent.id,
                "decision_kind": "question",
                "decision_request_id": crash_request["decision_request_id"],
                "request_identity": crash_identity,
                "response": {
                    "mode": "single",
                    "selections": [{"id": "blue", "label": "Blue"}],
                },
                "decided_by": "test-user",
            }
        )
    agent.answer_verified_question.side_effect = None
    agent.answer_verified_question.return_value = True
    await StructuredDecisionRouter(runtime).observe(agent, crash_snapshot)
    assert agent.answer_verified_question.await_args.kwargs["operation_id"] == crash_request[
        "decision_request_id"
    ]

    assert agent.answer_verified_question.await_count == EXPECTED_QUESTION_EXECUTIONS
    agent.answer_verified_permission.assert_awaited_once()
    response_count = db.execute(
        "SELECT count(*) FROM structured_decisions WHERE response_json IS NOT NULL"
    ).fetchone()[0]
    assert response_count == len(snapshots) + 1


@pytest.mark.asyncio
async def test_permission_observe_and_respond_bridge_permission_service(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed dialogs create approval records; answers issue authorization proofs."""

    monkeypatch.setattr(
        "murder.runtime.terminal.tmux.send_keys",
        AsyncMock(side_effect=AssertionError("the decision router emitted terminal input")),
    )
    db = get_db(tmp_path / "murder.db")
    init_db(db)
    db.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        ("run-perm", "2026-07-12T00:00:00+00:00", "{}"),
    )
    bus = OrchestrationNotifier("run-perm", db)
    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    revision = ObservationRevision(1, 1, 1)
    base = unknown_snapshot(HarnessId("codex"), captured_at=now, revision=revision)
    permission = PermissionRequestState(
        "p-bridge",
        "shell",
        "pytest -q",
        "Run tests",
        (
            ChoiceState("allow_once", "Allow once", disabled=False, highlighted=True),
            ChoiceState("deny", "Deny", disabled=False),
        ),
        "allow_once",
        frozenset({"shell"}),
    )
    snapshot = replace(
        base,
        permission_request=Observed.present(
            permission, evidence=(), observed_at=now, revision=revision
        ),
    )
    agent = SimpleNamespace(
        id="bridge-agent",
        latest_ingested_frame=SimpleNamespace(snapshot=snapshot),
        answer_verified_question=AsyncMock(return_value=True),
        answer_verified_permission=AsyncMock(return_value=True),
    )
    runtime = SimpleNamespace(
        db=db,
        bus=bus,
        run_id="run-perm",
        get_agent=lambda agent_id: agent if agent_id == agent.id else None,
    )
    router = StructuredDecisionRouter(runtime)
    await router.observe(agent, snapshot)

    row = db.execute(
        "SELECT decision_request_id, decision_kind, request_identity, request_json "
        "FROM structured_decisions"
    ).fetchone()
    assert row is not None
    request = {
        "decision_request_id": row["decision_request_id"],
        "decision_kind": row["decision_kind"],
        "request_identity": row["request_identity"],
        "request": json.loads(row["request_json"]),
    }
    assert request["decision_kind"] == "permission"
    pending = PermissionStore(db).get_pending_approval_for_operation(
        UUID(request["decision_request_id"])
    )
    assert pending is not None
    assert pending[1].status == "pending"
    assert pending[1].requested_by == PermissionPrincipal(kind="llm", id=agent.id)

    accepted = await router.respond(
        {
            "agent_id": agent.id,
            "decision_kind": "permission",
            "decision_request_id": request["decision_request_id"],
            "request_identity": permission_fingerprint(permission),
            "response": {
                "kind": "allow_once",
                "id": "allow_once",
                "label": "Allow once",
            },
            "decided_by": "human-reviewer",
        }
    )
    assert accepted == {"ok": True}
    agent.answer_verified_permission.assert_awaited_once()
    store = PermissionStore(db)
    assert store.get_pending_approval_for_operation(UUID(request["decision_request_id"])) is None
    assert store.count("permission_authorization_grants") == 1
    assert store.count("permission_approval_evidence") == 1
