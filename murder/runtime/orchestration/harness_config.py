"""Crow harness resolution concern extracted from the Orchestrator (move-code refactor)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from murder.config import (
    resolve_default_crow_harness,
    resolve_default_crow_startup_effort,
    resolve_default_crow_startup_model,
)
from murder.llm.harnesses import get as get_harness

if TYPE_CHECKING:
    from murder.app.service.runtime_scope import OrchestratorHost


@dataclass(frozen=True)
class CrowHarness:
    kind: str
    startup_model: str | None
    startup_effort: str | None


class HarnessConfigurator:
    """Resolves crow harness/model/effort from config + ticket row."""

    def __init__(self, rt: OrchestratorHost) -> None:
        self.rt = rt

    def resolve_crow(self, row: dict[str, Any]) -> CrowHarness:
        kind = resolve_default_crow_harness(self.rt.config.default_crow, row)
        return CrowHarness(
            kind,
            resolve_default_crow_startup_model(self.rt.config.default_crow, row, kind),
            resolve_default_crow_startup_effort(self.rt.config.default_crow, row),
        )

    def adapter(self, ch: CrowHarness) -> Any:
        return get_harness(
            ch.kind,
            startup_model=ch.startup_model,
            startup_effort=ch.startup_effort,
        )
