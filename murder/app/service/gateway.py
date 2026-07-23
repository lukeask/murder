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


class ApplicationGateway:
    """Validate the closed public request union and invoke application use cases."""

    def __init__(self, application: ApplicationPort) -> None:
        self._application = application

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
            result = await self._application.query(request.name, params)
            return self._validate_result(
                operation.result_model, result, capability=request.name.value
            )

        if request.name is CommandName.ORCHESTRATION_EXECUTE:
            operation = command_operation(request.name)
            params = self._validate_params(
                operation.params_model, params, capability=request.name.value
            )
            result = await self._application.command(request.name, params)
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
        result = await self._application.command(request.name, params)
        return self._validate_result(operation.result_model, result, capability=request.name.value)

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
