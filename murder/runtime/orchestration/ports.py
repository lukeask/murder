"""Narrow internal ports for orchestration commands, events, and diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from murder.runtime.orchestration.events import CommandEvent, OrchestrationEvent

if TYPE_CHECKING:
    from murder.observability.advanced_log import (
        ApiRecord,
        ArtifactRefRecord,
        CommandRecord,
        ExceptionRecord,
        ParserRecord,
        StateMutationRecord,
        TmuxFrameRecord,
    )


class CommandRepository(Protocol):
    """Durable storage for worker commands."""

    def add(self, command: CommandEvent) -> None: ...


class CommandSubmitter(Protocol):
    """Application-facing command submission capability."""

    async def submit(self, command: CommandEvent) -> None: ...


class OrchestrationEventSink(Protocol):
    """Ephemeral, best-effort notification capability."""

    async def publish(self, event: OrchestrationEvent) -> None: ...


class AdvancedLogSink(Protocol):
    """Write-only flight-recorder capability used by runtime components."""

    def record_orchestration_event(self, event: OrchestrationEvent) -> None: ...

    def record_api(self, record: ApiRecord) -> None: ...

    def record_tmux_frame(self, record: TmuxFrameRecord) -> None: ...

    def record_parser(self, record: ParserRecord) -> None: ...

    def record_command(self, record: CommandRecord) -> None: ...

    def record_state_mutation(self, record: StateMutationRecord) -> None: ...

    def record_artifact_ref(self, record: ArtifactRefRecord) -> None: ...

    def record_exception(self, record: ExceptionRecord) -> None: ...


__all__ = [
    "AdvancedLogSink",
    "CommandRepository",
    "CommandSubmitter",
    "OrchestrationEventSink",
]
