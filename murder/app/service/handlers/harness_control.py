"""Application boundary for externally decided verified harness interactions."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.lifecycle import HarnessAnswerParams
from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter


def register(app: ApplicationRegistrar, decisions: StructuredDecisionRouter) -> None:
    async def _answer_structured(body: dict[str, Any]) -> dict[str, object]:
        params = HarnessAnswerParams.model_validate(body)
        return await decisions.respond(params.model_dump(mode="json"))

    app.register_application_command(CommandName.HARNESS_ANSWER, _answer_structured)
