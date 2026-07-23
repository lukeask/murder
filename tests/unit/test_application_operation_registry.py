"""Registry-driven application protocol validation and capabilities."""

from __future__ import annotations

import pytest

from murder.app.protocol.operations import COMMAND_OPERATIONS, QUERY_OPERATIONS
from murder.app.protocol.requests import CommandName, QueryName, QueryRequest
from murder.app.service.gateway import ApplicationGateway


class _Application:
    available_queries = (QueryName.APPROVALS_LIST,)
    available_commands = (CommandName.WORKFLOW_START,)

    def __init__(self) -> None:
        self.called = False

    async def query(self, name: QueryName, params: dict[str, object]) -> dict[str, object]:
        self.called = True
        return {"approvals": "not-a-list"}

    async def command(self, name: CommandName, params: dict[str, object]) -> dict[str, object]:
        self.called = True
        return {}


def test_registry_covers_the_entire_closed_capability_vocabulary() -> None:
    assert set(QUERY_OPERATIONS) == set(QueryName)
    assert set(COMMAND_OPERATIONS) == set(CommandName)


def test_gateway_capabilities_come_from_installed_application_handlers() -> None:
    gateway = ApplicationGateway(_Application())

    assert gateway.available_queries == (QueryName.APPROVALS_LIST,)
    assert gateway.available_commands == (CommandName.WORKFLOW_START,)


@pytest.mark.asyncio
async def test_gateway_validates_selected_request_and_reply_models() -> None:
    application = _Application()
    gateway = ApplicationGateway(application)

    with pytest.raises(ValueError, match="invalid params for approvals.list"):
        await gateway.request(
            QueryRequest(name=QueryName.APPROVALS_LIST, params={"workflow_id": "nope"}),
            timeout_s=1,
        )
    assert application.called is False

    with pytest.raises(ValueError, match="invalid result for approvals.list"):
        await gateway.request(QueryRequest(name=QueryName.APPROVALS_LIST), timeout_s=1)
    assert application.called is True
