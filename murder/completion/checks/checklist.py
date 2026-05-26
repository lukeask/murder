"""ChecklistCheck — verifies all checklist items are marked done."""

from __future__ import annotations

from murder.enforcement.checklist_verify import format_report, verify_checklist

from .base import CheckResult, CheckStatus, CompletionContext


class ChecklistCheck:
    name = "checklist"

    async def run(self, ctx: CompletionContext) -> CheckResult:
        report = verify_checklist(ctx.db, ctx.ticket_id, ctx.repo_root)
        if report.overall_ok:
            return CheckResult(CheckStatus.PASS, message="checklist verified")
        detail = format_report(report)
        return CheckResult(
            CheckStatus.FAIL,
            message="checklist verification failed",
            hint=f"Complete all checklist items:\n{detail}",
        )


__all__ = ["ChecklistCheck"]
