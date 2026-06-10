"""Role-generic briefing system: BriefContext → assembled prompt string."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from murder.runtime.agents.base import AgentRole
from murder.llm.harnesses.capabilities import HarnessCapabilities
from murder.state.storage.paths import crow_context_dir, ticket_md


@dataclass(frozen=True)
class BriefContext:
    role: AgentRole
    repo_root: Path
    caps: HarnessCapabilities
    harness_name: str
    model: str | None
    ticket: dict | None = None
    plan_name: str | None = None


@dataclass(frozen=True)
class Block:
    heading: str | None  # None = raw text; str = injected as "## {heading}"
    text: str


class Section(Protocol):
    def build(self, ctx: BriefContext) -> Block | None: ...


class BriefAssembler:
    def __init__(self, template_name: str, sections: list[Section]) -> None:
        self._template_name = template_name
        self._sections = sections

    def build(self, ctx: BriefContext) -> str:
        from murder.llm.prompts import load, render

        vars = _template_vars(ctx)
        try:
            system = render(self._template_name, **vars)
        except (KeyError, IndexError, ValueError):
            system = load(self._template_name)

        parts: list[str] = [system]
        for section in self._sections:
            block = section.build(ctx)
            if block is None:
                continue
            parts.append("")
            if block.heading is not None:
                parts.append(f"## {block.heading}")
            if block.text:
                parts.append(block.text)
        return "\n".join(parts)


def _template_vars(ctx: BriefContext) -> dict[str, str]:
    ticket_id = ctx.ticket["id"] if ctx.ticket else ""
    return {
        "ticket_id": ticket_id,
        "ticket_path": str(ticket_md(ctx.repo_root, ticket_id)) if ticket_id else "",
        "plan_name": ctx.plan_name or "",
        "harness": ctx.harness_name,
        "model": ctx.model or "",
    }


class RepoDocumentsSection:
    """Inject `.murder/context/*.md` files when present and non-empty."""

    def build(self, ctx: BriefContext) -> Block | None:
        context_dir = crow_context_dir(ctx.repo_root)
        if not context_dir.is_dir():
            return None
        blocks: list[str] = []
        for doc in sorted(context_dir.glob("*.md")):
            try:
                content = doc.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content:
                blocks.append(f"## {doc.stem}\n{content}")
        if not blocks:
            return None
        return Block(heading=None, text="\n\n".join(blocks))


class HarnessQuirksSection:
    """Inject harness/model-specific corrections when a quirks file exists.

    Lookup order:
      1. `.murder/context/quirks/{harness_name}-{model_slug}.md`
      2. `.murder/context/quirks/{harness_name}.md`

    Returns None if neither exists or both are empty. The quirks directory is
    only created when there's actually a known quirk to document.
    """

    def build(self, ctx: BriefContext) -> Block | None:
        quirks_dir = ctx.repo_root / ".murder" / "context" / "quirks"
        if not quirks_dir.is_dir():
            return None
        candidates: list[Path] = []
        if ctx.model:
            model_slug = ctx.model.replace("/", "-").replace(":", "-")
            candidates.append(quirks_dir / f"{ctx.harness_name}-{model_slug}.md")
        candidates.append(quirks_dir / f"{ctx.harness_name}.md")
        for path in candidates:
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if content:
                return Block(heading=None, text=content)
        return None


class SubagentHintSection:
    """Inject subagent usage hint when the harness declares subagent support."""

    def build(self, ctx: BriefContext) -> Block | None:
        if not ctx.caps.supports_subagents:
            return None
        hint = "You can spawn subagents."
        if ctx.caps.cheapest_subagent_model:
            hint += f" Use `{ctx.caps.cheapest_subagent_model}` for cheap/fast subtasks."
        return Block(heading=None, text=hint)


class CurrentPlanSection:
    """Inject the plan markdown into a planner brief when the plan already exists."""

    def build(self, ctx: BriefContext) -> Block | None:
        if not ctx.plan_name:
            return None
        plan_path = ctx.repo_root / ".murder" / "plans" / f"{ctx.plan_name}.md"
        if not plan_path.exists():
            return None
        try:
            content = plan_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not content:
            return None
        return Block(heading=ctx.plan_name, text=content)


def assembler_for(ctx: BriefContext) -> BriefAssembler:
    match ctx.role:
        case AgentRole.CROW:
            return BriefAssembler(
                "fresh_crow",
                [
                    RepoDocumentsSection(),
                    HarnessQuirksSection(),
                    SubagentHintSection(),
                ],
            )
        case AgentRole.PLANNER:
            return BriefAssembler(
                "planner",
                [
                    CurrentPlanSection(),
                    RepoDocumentsSection(),
                ],
            )
        case AgentRole.COLLABORATOR:
            return BriefAssembler(
                "collaborator",
                [
                    RepoDocumentsSection(),
                ],
            )
        case _:
            raise ValueError(f"no assembler defined for role {ctx.role!r}")


__all__ = [
    "BriefAssembler",
    "BriefContext",
    "Block",
    "CurrentPlanSection",
    "HarnessQuirksSection",
    "RepoDocumentsSection",
    "Section",
    "SubagentHintSection",
    "assembler_for",
]
