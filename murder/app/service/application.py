"""Typed, in-process application dispatch.

The public application protocol terminates here.  A request is selected by its
closed enum and invokes the feature handler directly; no bus event, RPC target,
or broker participates in normal application dispatch.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from murder.app.protocol.requests import CommandName, QueryName

ApplicationHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class ApplicationPort(Protocol):
    """Use-case port consumed by :class:`ApplicationGateway`."""

    async def query(self, name: QueryName, params: dict[str, Any]) -> dict[str, Any]: ...

    async def command(self, name: CommandName, params: dict[str, Any]) -> dict[str, Any]: ...


class ApplicationDispatcher:
    """Enum-keyed application service composed from feature handlers."""

    def __init__(
        self,
        *,
        queries: Mapping[QueryName, ApplicationHandler],
        commands: Mapping[CommandName, ApplicationHandler],
    ) -> None:
        missing_queries = set(QueryName) - set(queries)
        missing_commands = set(CommandName) - set(commands)
        if missing_queries or missing_commands:
            raise RuntimeError(
                "incomplete application dispatch: "
                f"queries={sorted(item.value for item in missing_queries)}, "
                f"commands={sorted(item.value for item in missing_commands)}"
            )
        self._queries = dict(queries)
        self._commands = dict(commands)

    @property
    def available_queries(self) -> tuple[QueryName, ...]:
        return tuple(sorted(self._queries, key=lambda name: name.value))

    @property
    def available_commands(self) -> tuple[CommandName, ...]:
        return tuple(sorted(self._commands, key=lambda name: name.value))

    async def query(self, name: QueryName, params: dict[str, Any]) -> dict[str, Any]:
        return await _invoke(self._queries[name], params)

    async def command(self, name: CommandName, params: dict[str, Any]) -> dict[str, Any]:
        return await _invoke(self._commands[name], params)


async def _invoke(handler: ApplicationHandler, params: dict[str, Any]) -> dict[str, Any]:
    result = handler(params)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = ["ApplicationDispatcher", "ApplicationHandler", "ApplicationPort"]
