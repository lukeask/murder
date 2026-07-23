"""RPC boundary for externally decided verified harness interactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    async def _answer_structured(body: dict[str, Any]) -> dict[str, object]:
        runtime = host.runtime
        router = getattr(runtime, "structured_decisions", None) if runtime is not None else None
        if router is None:
            raise RuntimeError("structured decision routing is unavailable")
        return await router.respond(body)

    host.register_application_command(CommandName.HARNESS_ANSWER, _answer_structured)
