"""Durable routing between verified harness surfaces and external decisions.

This module owns no policy and emits no terminal effects.  It publishes the
semantic request visible in a persisted observation, validates a later
user/policy response against the still-current identity, records that response,
and delegates execution to the verified capability on the owning agent.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

from murder.bus import HarnessDecisionRequestEvent, HarnessDecisionResponseEvent
from murder.llm.harness_control.capabilities.permissions import (
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
    permission_fingerprint,
)
from murder.llm.harness_control.capabilities.questions import (
    QuestionAnswerRequest,
    question_fingerprint,
)
from murder.llm.harness_control.model.actions import (
    QuestionAnswerMode,
    QuestionChoiceSelection,
)
from murder.llm.harness_control.model.observations import Knowledge, ObservationSnapshot


class StructuredDecisionHost(Protocol):
    db: Any
    bus: Any
    run_id: str | None

    def get_agent(self, agent_id: str) -> Any | None: ...


DecisionKind = Literal["question", "permission"]


def _choice_payload(choice: Any) -> dict[str, object]:
    return {
        "id": choice.stable_choice_id,
        "label": choice.label,
        "description": choice.description,
        "number": choice.number,
        "shortcut": choice.shortcut,
        "selected": choice.selected,
        "highlighted": choice.highlighted,
        "checked": choice.checked,
        "disabled": choice.disabled,
        "current": choice.current,
    }


class StructuredDecisionRouter:
    """Identity-bound durable request/response seam for structured controls."""

    def __init__(self, host: StructuredDecisionHost) -> None:
        self._host = host
        self._visible: dict[tuple[str, DecisionKind], str] = {}
        self._cleared: set[tuple[str, DecisionKind]] = set()

    async def observe(self, agent: Any, snapshot: ObservationSnapshot) -> None:
        """Publish each currently visible normalized request exactly once."""

        if self._host.bus is None or self._host.db is None or self._host.run_id is None:
            return
        await self._resume_recorded_responses(agent, snapshot)
        candidates: list[tuple[DecisionKind, str, dict[str, object]]] = []
        if snapshot.question.knowledge is Knowledge.PRESENT and snapshot.question.value is not None:
            question = snapshot.question.value
            candidates.append(
                (
                    "question",
                    question_fingerprint(question),
                    {
                        "request_id_hint": question.question_id_hint,
                        "prompt_text": question.prompt_text,
                        "choices": [_choice_payload(choice) for choice in question.choices],
                        "selection_mode": question.selection_mode,
                        "allow_custom_answer": question.allow_custom_answer,
                        "submit_label": question.submit_label,
                        "decline_label": question.decline_label,
                    },
                )
            )
        if (
            snapshot.permission_request.knowledge is Knowledge.PRESENT
            and snapshot.permission_request.value is not None
        ):
            permission = snapshot.permission_request.value
            candidates.append(
                (
                    "permission",
                    permission_fingerprint(permission),
                    {
                        "request_id_hint": permission.request_id_hint,
                        "tool_name": permission.tool_name,
                        "command": permission.command,
                        "description": permission.description,
                        "choices": [_choice_payload(choice) for choice in permission.choices],
                        "risk_attributes": sorted(permission.risk_attributes),
                    },
                )
            )

        explicitly_absent = {
            "question": snapshot.question.knowledge is Knowledge.ABSENT,
            "permission": snapshot.permission_request.knowledge is Knowledge.ABSENT,
        }
        for kind in ("question", "permission"):
            key = (agent.id, kind)
            if explicitly_absent[kind] and key in self._visible:
                self._visible.pop(key, None)
                self._cleared.add(key)
            elif explicitly_absent[kind] and self._has_kind_history(agent.id, kind):
                # Reconstruct an absence edge after service restart from the
                # durable fact that this surface kind occurred previously.
                self._cleared.add(key)

        revision = snapshot.revision
        for kind, identity, request in candidates:
            key = (agent.id, kind)
            if self._visible.get(key) == identity:
                continue
            previous = self._visible.get(key)
            reopened = key in self._cleared
            self._visible[key] = identity
            self._cleared.discard(key)
            if previous is None and not reopened and self._has_any_occurrence(
                agent.id, kind, identity
            ):
                # On service restart, the absence edge cannot be invented. A
                # lingering terminal dialog remains the prior occurrence until
                # a real absent/different observation is seen.
                continue
            occurrence = (
                f"{revision.pane_epoch}:{revision.capture_sequence}:"
                f"{revision.semantic_sequence}"
            )
            request_id = str(
                uuid5(NAMESPACE_URL, f"{agent.id}:{kind}:{identity}:{occurrence}")
            )
            await self._host.bus.publish(
                HarnessDecisionRequestEvent(
                    run_id=self._host.run_id,
                    agent_id=agent.id,
                    role=getattr(agent, "role", None),
                    ticket_id=getattr(agent, "ticket_id", None),
                    decision_request_id=request_id,
                    decision_kind=kind,
                    request_identity=identity,
                    observation_revision=(
                        revision.pane_epoch,
                        revision.capture_sequence,
                        revision.semantic_sequence,
                    ),
                    request=request,
                )
            )

    async def respond(self, body: dict[str, Any]) -> dict[str, object]:  # noqa: PLR0911
        """Record and execute an exact response, rejecting stale decisions."""

        agent_id = str(body.get("agent_id") or "").strip()
        request_id = str(body.get("decision_request_id") or "").strip()
        kind = str(body.get("decision_kind") or "").strip()
        identity = str(body.get("request_identity") or "").strip()
        decided_by = str(body.get("decided_by") or "").strip()
        response = body.get("response")
        if (
            kind not in {"question", "permission"}
            or not all((agent_id, request_id, identity, decided_by))
            or not isinstance(response, dict)
        ):
            return {"ok": False, "error": "invalid_decision_response"}

        persisted = self._load_request(request_id)
        if persisted is None or persisted.get("agent_id") != agent_id:
            return {"ok": False, "error": "decision_request_not_found"}
        if persisted["decision_kind"] != kind:
            return {"ok": False, "error": "decision_kind_mismatch"}
        if persisted["request_identity"] != identity:
            return {"ok": False, "error": "request_identity_mismatch"}

        agent = self._host.get_agent(agent_id)
        ingested = getattr(agent, "latest_ingested_frame", None) if agent is not None else None
        snapshot = getattr(ingested, "snapshot", None)
        if snapshot is None or self._current_identity(kind, snapshot) != identity:
            return {"ok": False, "error": "request_not_current"}
        if self._response_exists(request_id):
            return {"ok": False, "error": "response_already_recorded"}

        semantic_request = self._decode_response(kind, identity, persisted["request"], response)
        if semantic_request is None:
            return {"ok": False, "error": "invalid_semantic_response"}
        await self._host.bus.publish(
            HarnessDecisionResponseEvent(
                run_id=self._host.run_id,
                agent_id=agent_id,
                role=getattr(agent, "role", None),
                ticket_id=getattr(agent, "ticket_id", None),
                decision_request_id=request_id,
                decision_kind=kind,
                request_identity=identity,
                response=response,
                decided_by=decided_by,
            )
        )
        executed = (
            await agent.answer_verified_question(semantic_request, operation_id=request_id)
            if kind == "question"
            else await agent.answer_verified_permission(semantic_request, operation_id=request_id)
        )
        return {"ok": True} if executed else {"ok": False, "error": "execution_not_verified"}

    async def _resume_recorded_responses(
        self, agent: Any, snapshot: ObservationSnapshot
    ) -> None:
        """Start decisions recorded before a crash when no operation exists yet."""

        rows = self._host.db.execute(
            "SELECT payload_json FROM events "
            "WHERE type = 'harness.decision.response' AND agent_id = ? ORDER BY id",
            (agent.id,),
        ).fetchall()
        for row in rows:
            response_event = json.loads(row["payload_json"])
            request_id = str(response_event.get("decision_request_id") or "")
            if not request_id or self._operation_exists(request_id):
                continue
            persisted = self._load_request(request_id)
            kind = str(response_event.get("decision_kind") or "")
            identity = str(response_event.get("request_identity") or "")
            if (
                persisted is None
                or persisted.get("agent_id") != agent.id
                or persisted.get("decision_kind") != kind
                or persisted.get("request_identity") != identity
                or self._current_identity(kind, snapshot) != identity
            ):
                continue
            response = response_event.get("response")
            if not isinstance(response, dict):
                continue
            semantic_request = self._decode_response(kind, identity, persisted["request"], response)
            if semantic_request is None:
                continue
            if kind == "question":
                await agent.answer_verified_question(semantic_request, operation_id=request_id)
            else:
                await agent.answer_verified_permission(semantic_request, operation_id=request_id)

    def _operation_exists(self, operation_id: str) -> bool:
        return (
            self._host.db.execute(
                "SELECT 1 FROM harness_control_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            is not None
        )

    def _has_any_occurrence(self, agent_id: str, kind: str, identity: str) -> bool:
        row = self._host.db.execute(
            "SELECT payload_json FROM events WHERE type = 'harness.decision.request' "
            "AND agent_id = ? "
            "AND json_extract(payload_json, '$.decision_kind') = ? "
            "AND json_extract(payload_json, '$.request_identity') = ? "
            "ORDER BY id DESC LIMIT 1",
            (agent_id, kind, identity),
        ).fetchone()
        return row is not None

    def _has_kind_history(self, agent_id: str, kind: str) -> bool:
        return (
            self._host.db.execute(
                "SELECT 1 FROM events WHERE type = 'harness.decision.request' "
                "AND agent_id = ? "
                "AND json_extract(payload_json, '$.decision_kind') = ? LIMIT 1",
                (agent_id, kind),
            ).fetchone()
            is not None
        )

    def _response_exists(self, request_id: str) -> bool:
        return (
            self._host.db.execute(
                "SELECT 1 FROM events WHERE type = 'harness.decision.response' "
                "AND json_extract(payload_json, '$.decision_request_id') = ? LIMIT 1",
                (request_id,),
            ).fetchone()
            is not None
        )

    def _load_request(self, request_id: str) -> dict[str, Any] | None:
        row = self._host.db.execute(
            "SELECT agent_id, payload_json FROM events "
            "WHERE type = 'harness.decision.request' "
            "AND json_extract(payload_json, '$.decision_request_id') = ? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["agent_id"] = str(row["agent_id"] or "")
        return payload

    @staticmethod
    def _current_identity(kind: str, snapshot: ObservationSnapshot) -> str | None:
        if kind == "question":
            observed = snapshot.question
            return (
                question_fingerprint(observed.value)
                if observed.knowledge is Knowledge.PRESENT and observed.value is not None
                else None
            )
        observed = snapshot.permission_request
        return (
            permission_fingerprint(observed.value)
            if observed.knowledge is Knowledge.PRESENT and observed.value is not None
            else None
        )

    @staticmethod
    def _decode_response(
        kind: str,
        identity: str,
        persisted_request: dict[str, Any],
        response: dict[str, Any],
    ) -> QuestionAnswerRequest | PermissionAnswerRequest | None:
        try:
            if kind == "question":
                mode = QuestionAnswerMode[str(response["mode"]).strip().upper()]
                selections = tuple(
                    QuestionChoiceSelection(item.get("id"), str(item.get("label") or ""))
                    for item in response.get("selections", [])
                    if isinstance(item, dict)
                )
                return QuestionAnswerRequest(
                    persisted_request.get("request_id_hint"),
                    identity,
                    mode,
                    selections,
                    response.get("custom_answer"),
                )
            decision_kind = PermissionDecisionKind[str(response["kind"]).strip().upper()]
            return PermissionAnswerRequest(
                persisted_request.get("request_id_hint"),
                identity,
                PermissionResponseTarget(
                    response.get("id"), str(response.get("label") or ""), decision_kind
                ),
                frozenset(persisted_request.get("risk_attributes", [])),
            )
        except (KeyError, TypeError, ValueError):
            return None


__all__ = ["StructuredDecisionRouter"]
