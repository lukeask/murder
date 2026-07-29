"""Application protocol boundary over typed in-process use cases.

Clients can select only the closed capabilities declared in
``murder.app.protocol.requests``. The gateway validates and enriches the wire
request, then invokes an application port without knowing about transports,
brokers, worker targets, or feature implementation details.

High-risk capabilities validate params (and results) against typed protocol
contracts. Legacy callers may still pass plain dictionaries; the gateway
adapts them at this boundary.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import TypeAdapter, ValidationError

from murder.app.protocol.operations import command_operation, query_operation
from murder.app.protocol.requests import CommandName, CommandRequest, QueryName, QueryRequest
from murder.app.service.application import ApplicationPort
from murder.contracts.common import domain_request_id

_SESSION_WRITER_COMMANDS = frozenset(
    {
        CommandName.SESSION_WRITER_ACQUIRE,
        CommandName.SESSION_WRITER_RENEW,
        CommandName.SESSION_WRITER_RELEASE,
    }
)

_TRUSTED_LOCAL_HOLDER = {"kind": "service", "id": "trusted-local"}

_CORRELATED_COMMANDS = frozenset(
    {
        CommandName.SESSION_WRITER_ACQUIRE,
        CommandName.SESSION_WRITER_RENEW,
        CommandName.SESSION_WRITER_RELEASE,
        CommandName.SESSION_COMMAND_EXECUTE,
        CommandName.APPROVAL_DECIDE,
        CommandName.WORKFLOW_START,
        CommandName.WORKFLOW_SIGNAL,
    }
)

PlanSeedScheduler = Callable[[str, str, str | None], None]


class ApplicationGateway:
    """Validate the closed public request union and invoke application use cases."""

    def __init__(
        self,
        application: ApplicationPort,
        *,
        schedule_plan_seed: PlanSeedScheduler | None = None,
    ) -> None:
        self._application = application
        self._schedule_plan_seed = schedule_plan_seed

    @property
    def available_queries(self) -> tuple[QueryName, ...]:
        names = getattr(self._application, "available_queries", tuple(QueryName))
        return tuple(sorted(names, key=lambda item: item.value))

    @property
    def available_commands(self) -> tuple[CommandName, ...]:
        names = getattr(self._application, "available_commands", tuple(CommandName))
        return tuple(sorted(names, key=lambda item: item.value))

    async def request(
        self,
        request: QueryRequest | CommandRequest,
        *,
        timeout_s: float,
        authenticated_client_id: str | None = None,
        wire_request_id: str | None = None,
    ) -> dict[str, Any]:
        params = dict(request.params)
        if isinstance(request, QueryRequest):
            operation = query_operation(request.name)
            params = self._validate_params(
                operation.params_model, params, capability=request.name.value
            )
            result = await self._await_with_timeout(
                self._application.query(request.name, params), timeout_s
            )
            return self._validate_result(
                operation.result_model, result, capability=request.name.value
            )

        if request.name is CommandName.APPROVAL_DECIDE:
            if authenticated_client_id is None:
                raise ValueError("approval.decide requires an authenticated client")
            params["reviewer"] = {
                "kind": "client",
                "id": authenticated_client_id,
            }
        elif request.name in _SESSION_WRITER_COMMANDS:
            if authenticated_client_id is not None:
                params["holder"] = {
                    "kind": "client",
                    "id": authenticated_client_id,
                }
            else:
                params["holder"] = dict(_TRUSTED_LOCAL_HOLDER)
        elif request.name is CommandName.SESSION_COMMAND_EXECUTE:
            if authenticated_client_id is not None:
                params["principal"] = {
                    "kind": "client",
                    "id": authenticated_client_id,
                }
            else:
                params["principal"] = dict(_TRUSTED_LOCAL_HOLDER)

        if request.name in _CORRELATED_COMMANDS and params.get("request_id") is None:
            params["request_id"] = str(domain_request_id(wire_request_id=wire_request_id))

        operation = command_operation(request.name)
        params = self._validate_params(
            operation.params_model, params, capability=request.name.value
        )
        result = await self._await_with_timeout(
            self._application.command(request.name, params), timeout_s
        )
        validated = self._validate_result(
            operation.result_model, result, capability=request.name.value
        )
        self._schedule_plan_seed_if_needed(
            request.name, params, validated, authenticated_client_id
        )
        return validated

    async def _await_with_timeout(
        self, awaitable: Awaitable[dict[str, Any]], timeout_s: float
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout_s)
        except TimeoutError as exc:
            raise TimeoutError(f"request timed out after {timeout_s:g}s") from exc

    def _schedule_plan_seed_if_needed(
        self,
        name: CommandName,
        params: dict[str, object],
        result: dict[str, Any],
        client_id: str | None,
    ) -> None:
        if name is not CommandName.PLAN_CREATE or self._schedule_plan_seed is None:
            return
        message = str(params.get("message") or "").strip()
        plan_name = str(result.get("plan_name") or "").strip()
        if message and plan_name:
            self._schedule_plan_seed(plan_name, message, client_id)

    @staticmethod
    def _validate_params(
        model: object,
        params: dict[str, object],
        *,
        capability: str,
    ) -> dict[str, object]:
        try:
            validated = TypeAdapter(model).validate_python(params)
            return TypeAdapter(model).dump_python(validated, mode="json", exclude_none=False)
        except ValidationError as exc:
            raise ValueError(f"invalid params for {capability}: {exc}") from exc

    @staticmethod
    def _validate_result(
        model: object,
        result: dict[str, Any],
        *,
        capability: str,
    ) -> dict[str, Any]:
        try:
            validated = TypeAdapter(model).validate_python(result)
            return TypeAdapter(model).dump_python(validated, mode="json", exclude_none=False)
        except ValidationError as exc:
            raise ValueError(f"invalid result for {capability}: {exc}") from exc
