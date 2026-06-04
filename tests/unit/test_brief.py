"""Tests for the BriefAssembler / Section system in murder.runtime.orchestration.brief.

Each section is tested in isolation (no template loading required).
Integration smoke-tests call assembler_for(...).build(...) with real templates
and assert that section-specific content appears in the output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.runtime.agents.base import AgentRole
from murder.llm.harnesses.capabilities import HarnessCapabilities
from murder.runtime.orchestration.brief import (
    BriefAssembler,
    BriefContext,
    CurrentPlanSection,
    HarnessQuirksSection,
    RepoDocumentsSection,
    SubagentHintSection,
    assembler_for,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_BARE_CAPS = HarnessCapabilities()
_SUBAGENT_CAPS = HarnessCapabilities(supports_subagents=True, cheapest_subagent_model="haiku")


def _ctx(
    role: AgentRole = AgentRole.CROW,
    *,
    repo_root: Path,
    caps: HarnessCapabilities = _BARE_CAPS,
    harness_name: str = "claude_code",
    model: str | None = "sonnet",
    ticket: dict | None = None,
    plan_name: str | None = None,
) -> BriefContext:
    return BriefContext(
        role=role,
        repo_root=repo_root,
        caps=caps,
        harness_name=harness_name,
        model=model,
        ticket=ticket,
        plan_name=plan_name,
    )


def _ticket() -> dict:
    return {
        "id": "t001",
        "title": "Add logging",
        "wave": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RepoDocumentsSection
# ─────────────────────────────────────────────────────────────────────────────


class TestRepoDocumentsSection:
    sec = RepoDocumentsSection()

    def test_returns_none_when_dir_absent(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path)
        assert self.sec.build(ctx) is None

    def test_returns_none_when_dir_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".murder" / "context").mkdir(parents=True)
        ctx = _ctx(repo_root=tmp_path)
        assert self.sec.build(ctx) is None

    def test_returns_none_when_all_files_empty(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "notes.md").write_text("")
        ctx = _ctx(repo_root=tmp_path)
        assert self.sec.build(ctx) is None

    def test_heading_is_none(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "notes.md").write_text("some notes")
        ctx = _ctx(repo_root=tmp_path)
        block = self.sec.build(ctx)
        assert block is not None
        assert block.heading is None

    def test_file_content_appears(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "conventions.md").write_text("always use type hints")
        ctx = _ctx(repo_root=tmp_path)
        block = self.sec.build(ctx)
        assert block is not None
        assert "always use type hints" in block.text

    def test_stem_used_as_sub_heading(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "architecture.md").write_text("layered design")
        ctx = _ctx(repo_root=tmp_path)
        block = self.sec.build(ctx)
        assert block is not None
        assert "## architecture" in block.text

    def test_multiple_files_alphabetical(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "beta.md").write_text("beta content")
        (ctx_dir / "alpha.md").write_text("alpha content")
        ctx = _ctx(repo_root=tmp_path)
        block = self.sec.build(ctx)
        assert block is not None
        assert block.text.index("alpha") < block.text.index("beta")


# ─────────────────────────────────────────────────────────────────────────────
# HarnessQuirksSection
# ─────────────────────────────────────────────────────────────────────────────


class TestHarnessQuirksSection:
    sec = HarnessQuirksSection()

    def test_returns_none_when_quirks_dir_absent(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path, harness_name="claude_code", model="sonnet")
        assert self.sec.build(ctx) is None

    def test_harness_level_fallback(self, tmp_path: Path) -> None:
        quirks_dir = tmp_path / ".murder" / "context" / "quirks"
        quirks_dir.mkdir(parents=True)
        (quirks_dir / "claude_code.md").write_text("use Esc not Ctrl+C")
        ctx = _ctx(repo_root=tmp_path, harness_name="claude_code", model="sonnet")
        block = self.sec.build(ctx)
        assert block is not None
        assert "use Esc not Ctrl+C" in block.text

    def test_model_specific_takes_priority(self, tmp_path: Path) -> None:
        quirks_dir = tmp_path / ".murder" / "context" / "quirks"
        quirks_dir.mkdir(parents=True)
        (quirks_dir / "claude_code.md").write_text("generic quirk")
        (quirks_dir / "claude_code-sonnet.md").write_text("model-specific quirk")
        ctx = _ctx(repo_root=tmp_path, harness_name="claude_code", model="sonnet")
        block = self.sec.build(ctx)
        assert block is not None
        assert "model-specific quirk" in block.text
        assert "generic quirk" not in block.text

    def test_returns_none_when_all_files_empty(self, tmp_path: Path) -> None:
        quirks_dir = tmp_path / ".murder" / "context" / "quirks"
        quirks_dir.mkdir(parents=True)
        (quirks_dir / "claude_code.md").write_text("")
        ctx = _ctx(repo_root=tmp_path, harness_name="claude_code", model=None)
        assert self.sec.build(ctx) is None

    def test_heading_is_none(self, tmp_path: Path) -> None:
        quirks_dir = tmp_path / ".murder" / "context" / "quirks"
        quirks_dir.mkdir(parents=True)
        (quirks_dir / "claude_code.md").write_text("some quirk")
        ctx = _ctx(repo_root=tmp_path, harness_name="claude_code", model=None)
        block = self.sec.build(ctx)
        assert block is not None
        assert block.heading is None


# ─────────────────────────────────────────────────────────────────────────────
# SubagentHintSection
# ─────────────────────────────────────────────────────────────────────────────


class TestSubagentHintSection:
    sec = SubagentHintSection()

    def test_returns_none_when_not_supported(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path, caps=HarnessCapabilities(supports_subagents=False))
        assert self.sec.build(ctx) is None

    def test_returns_block_when_supported(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path, caps=HarnessCapabilities(supports_subagents=True))
        assert self.sec.build(ctx) is not None

    def test_includes_model_name_when_set(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path, caps=_SUBAGENT_CAPS)
        block = self.sec.build(ctx)
        assert block is not None
        assert "haiku" in block.text

    def test_heading_is_none(self, tmp_path: Path) -> None:
        ctx = _ctx(repo_root=tmp_path, caps=HarnessCapabilities(supports_subagents=True))
        block = self.sec.build(ctx)
        assert block is not None
        assert block.heading is None


# ─────────────────────────────────────────────────────────────────────────────
# CurrentPlanSection
# ─────────────────────────────────────────────────────────────────────────────


class TestCurrentPlanSection:
    sec = CurrentPlanSection()

    def test_returns_none_when_no_plan_name(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name=None)
        assert self.sec.build(ctx) is None

    def test_returns_none_when_plan_file_absent(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".murder" / "plans"
        plans_dir.mkdir(parents=True)
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name="missing-plan")
        assert self.sec.build(ctx) is None

    def test_returns_block_when_plan_exists(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".murder" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "my-plan.md").write_text("# My Plan\nDo the thing.")
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name="my-plan")
        block = self.sec.build(ctx)
        assert block is not None
        assert "Do the thing." in block.text

    def test_heading_is_plan_name(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".murder" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "my-plan.md").write_text("content")
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name="my-plan")
        block = self.sec.build(ctx)
        assert block is not None
        assert block.heading == "my-plan"

    def test_returns_none_when_plan_file_empty(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".murder" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "empty-plan.md").write_text("")
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name="empty-plan")
        assert self.sec.build(ctx) is None


# ─────────────────────────────────────────────────────────────────────────────
# assembler_for() dispatch
# ─────────────────────────────────────────────────────────────────────────────


class TestAssemblerForDispatch:
    def test_crow_returns_brief_assembler(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path)
        assert isinstance(assembler_for(ctx), BriefAssembler)

    def test_planner_returns_brief_assembler(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path)
        assert isinstance(assembler_for(ctx), BriefAssembler)

    def test_collaborator_returns_brief_assembler(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.COLLABORATOR, repo_root=tmp_path)
        assert isinstance(assembler_for(ctx), BriefAssembler)

    def test_unknown_role_raises(self, tmp_path: Path) -> None:
        ctx = BriefContext(
            role="unknown_role",  # type: ignore[arg-type]
            repo_root=tmp_path,
            caps=_BARE_CAPS,
            harness_name="claude_code",
            model=None,
        )
        with pytest.raises(ValueError):
            assembler_for(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# Integration: build() output shape per role
# ─────────────────────────────────────────────────────────────────────────────


class TestBriefIntegration:
    def test_crow_build_is_string(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path, ticket=_ticket())
        output = assembler_for(ctx).build(ctx)
        assert isinstance(output, str)
        assert "Write set" not in output

    def test_crow_build_includes_subagent_hint(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path, caps=_SUBAGENT_CAPS)
        output = assembler_for(ctx).build(ctx)
        assert "subagent" in output.lower() or "haiku" in output

    def test_crow_build_no_subagent_hint_when_not_supported(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path, caps=_BARE_CAPS)
        output = assembler_for(ctx).build(ctx)
        assert "haiku" not in output

    def test_planner_build_includes_plan_content(self, tmp_path: Path) -> None:
        plans_dir = tmp_path / ".murder" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "my-plan.md").write_text("## Objectives\nDo important things.")
        ctx = _ctx(role=AgentRole.PLANNER, repo_root=tmp_path, plan_name="my-plan")
        output = assembler_for(ctx).build(ctx)
        assert "Do important things." in output

    def test_collaborator_build_is_string(self, tmp_path: Path) -> None:
        ctx = _ctx(role=AgentRole.COLLABORATOR, repo_root=tmp_path)
        output = assembler_for(ctx).build(ctx)
        assert isinstance(output, str)
        assert output.strip()

    def test_repo_documents_injected_in_crow_brief(self, tmp_path: Path) -> None:
        ctx_dir = tmp_path / ".murder" / "context"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "conventions.md").write_text("always type hint everything")
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path)
        output = assembler_for(ctx).build(ctx)
        assert "always type hint everything" in output

    def test_quirks_injected_in_crow_brief(self, tmp_path: Path) -> None:
        quirks_dir = tmp_path / ".murder" / "context" / "quirks"
        quirks_dir.mkdir(parents=True)
        (quirks_dir / "claude_code.md").write_text("use Esc to interrupt, not Ctrl+C")
        ctx = _ctx(role=AgentRole.CROW, repo_root=tmp_path, harness_name="claude_code")
        output = assembler_for(ctx).build(ctx)
        assert "use Esc to interrupt, not Ctrl+C" in output
