"""WriteSetCheck — verifies no changes outside the ticket's write_set."""

from __future__ import annotations

from murder.enforcement import git_diff

from .base import CheckResult, CheckStatus, CompletionContext


class WriteSetCheck:
    name = "writeset"

    async def run(self, ctx: CompletionContext) -> CheckResult:
        if not ctx.start_commit:
            return CheckResult(CheckStatus.PASS, message="no start commit — skip diff check")
        try:
            dirty = await git_diff.diff_outside(
                ctx.repo_root, ctx.start_commit, list(ctx.write_set)
            )
        except Exception as exc:
            return CheckResult(
                CheckStatus.FAIL,
                message="git diff check failed",
                hint=f"Fix the git diff error: {exc}",
            )
        if not dirty:
            return CheckResult(CheckStatus.PASS, message="diff within write_set")
        preview = str(dirty[:5])
        return CheckResult(
            CheckStatus.FAIL,
            message="diff outside write_set",
            # NOTE: no revert instruction — policy routes writeset failures to ASK_USER,
            # not REPROMPT. Never tell crows to `git checkout --` in the shared tree;
            # that destroys uncommitted user work. See policy.py resolution_policy().
            hint=f"Files modified outside write_set (requires human review): {preview}",
        )


__all__ = ["WriteSetCheck"]
