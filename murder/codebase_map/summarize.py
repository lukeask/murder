"""Per-file LLM summarizer (t058).

One source file in, one ``<file>.md`` summary out, under a *measured*
<=15% token budget. Reuses the existing APIClient + prompt-loading plumbing
(see ``murder.llm.harnesses.transcript_summarize``); this is the same shape
pointed at source instead of transcripts.

This is a clean single-file atom: no concurrency, no pre-warm, no semaphore.
The fan-out callsite (t059) owns those.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from murder.codebase_map.extract import Symbol, extract_symbols
from murder.codebase_map.tokens import REASONING_HEADROOM, count_tokens
from murder.llm.clients.base import APIClient
from murder.llm.prompts import render as render_prompt

SUMMARY_MODEL = "codebase-map-file"
BUDGET_FRACTION = 0.15
BUDGET_FLOOR = 128
NO_EXTRACTOR_NOTE = "no programmatic extractor — read the source below"
TRUNCATION_MARKER = " …[truncated]"


@dataclass
class FileSummary:
    """The markdown body for one source file plus its budget accounting."""

    path: str
    body: str  # the markdown that becomes <file>.md
    source_hash: str  # sha256 of src (staleness key, used by t060)
    source_tokens: int  # measured token count of src
    summary_tokens: int  # measured token count of body (<= budget)


class FileSummarizer:
    """Summarize a single source file to a budgeted markdown body."""

    def __init__(self, client: APIClient, *, rollup_client: APIClient | None = None) -> None:
        self.client = client
        self.rollup_client = rollup_client or client

    async def summarize(self, path: str, src: str) -> FileSummary:
        symbols = extract_symbols(path, src)
        symbols_text = _render_symbols(symbols)

        source_tokens = count_tokens(src)
        budget_tokens = max(math.ceil(BUDGET_FRACTION * source_tokens), BUDGET_FLOOR)

        body, summary_tokens = await self._complete(
            path=path,
            symbols_text=symbols_text,
            src=src,
            budget_tokens=budget_tokens,
        )

        if not body:
            # Even with reasoning headroom the model can occasionally burn
            # the whole cap thinking and emit no content. Retry once with the
            # cap doubled; the budget checks below still clamp the result.
            body, summary_tokens = await self._complete(
                path=path,
                symbols_text=symbols_text,
                src=src,
                budget_tokens=budget_tokens,
                max_tokens=(budget_tokens + REASONING_HEADROOM) * 2,
            )
            # Starved twice -> body stays "" with summary_tokens 0: an honest
            # empty summary, never a tighten/truncate of nothing.

        if summary_tokens > budget_tokens:
            # Re-prompt exactly once with the actual N vs limit M.
            body, summary_tokens = await self._complete(
                path=path,
                symbols_text=symbols_text,
                src=src,
                budget_tokens=budget_tokens,
                tighten=(summary_tokens, budget_tokens),
            )

        if summary_tokens > budget_tokens:
            # Still over after the retry — hard-truncate and never return over.
            body = _truncate_to_budget(body, budget_tokens)
            summary_tokens = count_tokens(body)

        return FileSummary(
            path=path,
            body=body,
            source_hash=hashlib.sha256(src.encode()).hexdigest(),
            source_tokens=source_tokens,
            summary_tokens=summary_tokens,
        )

    async def _complete(
        self,
        *,
        path: str,
        symbols_text: str,
        src: str,
        budget_tokens: int,
        tighten: tuple[int, int] | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, int]:
        system = render_prompt(
            "map_file_summary",
            path=path,
            symbols=symbols_text,
            source=src,
            budget_tokens=budget_tokens,
        )
        if tighten is not None:
            actual, limit = tighten
            user = (
                f"Your last summary was {actual} tokens, but the hard limit is "
                f"{limit} tokens. Rewrite it to fit strictly under {limit} tokens — "
                "cut detail, keep every signature."
            )
        else:
            user = "Write the summary now."

        result = await self.client.complete(
            model=SUMMARY_MODEL,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=None,
            # The provider cap covers reasoning + content on reasoning
            # models; the *content* budget is enforced by the prompt and the
            # local measurement below.
            max_tokens=(budget_tokens + REASONING_HEADROOM) if max_tokens is None else max_tokens,
            temperature=0.0,
        )
        body = (result.text or "").strip()
        # Measure the body locally: provider completion_tokens includes
        # reasoning tokens on reasoning models, which would mis-measure the
        # content and trigger spurious tighten/truncate passes.
        summary_tokens = count_tokens(body)
        return body, summary_tokens


def _render_symbols(symbols: list[Symbol] | None) -> str:
    if symbols is None:
        return NO_EXTRACTOR_NOTE
    if not symbols:
        return "(no symbols found)"
    lines = []
    for sym in symbols:
        line = f"- [{sym.kind}] {sym.signature}"
        if sym.docstring:
            line += f"  — {sym.docstring}"
        lines.append(line)
    return "\n".join(lines)


def _truncate_to_budget(body: str, budget_tokens: int) -> str:
    """Hard-truncate ``body`` so it fits under ``budget_tokens``.

    Uses the same ``len // 4`` equivalence as :func:`count_tokens`, reserving
    room for the truncation marker so the result stays under budget.
    """

    marker = TRUNCATION_MARKER
    marker_tokens = count_tokens(marker)
    char_budget = max(budget_tokens - marker_tokens, 0) * 4
    return body[:char_budget].rstrip() + marker


__all__ = ["FileSummary", "FileSummarizer"]
