"""Directory / root roll-ups for the codebase map (t059).

A ``DIR.md`` is a summary-of-summaries: one line per child file + a short
prose paragraph on how the directory's files relate, built from the child
*file* summaries (not raw source). ``ROOT.md`` is the same roll-up one level
up, built from the child ``DIR.md`` bodies.

Same client/prompt plumbing as the per-file summarizer (t058). Roll-ups read
already-small summaries, so they stay cheap and well under their inputs.
"""

from __future__ import annotations

import math

from murder.codebase_map.tokens import count_tokens
from murder.llm.clients.base import APIClient
from murder.llm.prompts import render as render_prompt

ROLLUP_MODEL = "codebase-map-rollup"

# A child entry is (name, the child's rendered markdown body).
ChildEntry = tuple[str, str]

# Roll-ups are summaries-of-summaries; keep the cap a fraction of the combined
# child input so the pyramid keeps compressing as it climbs.
ROLLUP_BUDGET_FRACTION = 0.5
ROLLUP_BUDGET_FLOOR = 128


def _render_children(children: list[ChildEntry]) -> str:
    blocks = []
    for name, body in children:
        blocks.append(f"### {name}\n{body.strip()}")
    return "\n\n".join(blocks)


def _budget(children: list[ChildEntry]) -> int:
    combined = count_tokens("\n\n".join(body for _, body in children))
    return max(math.ceil(ROLLUP_BUDGET_FRACTION * combined), ROLLUP_BUDGET_FLOOR)


async def _complete(client: APIClient, *, dir_path: str, children: list[ChildEntry]) -> str:
    budget = _budget(children)
    system = render_prompt(
        "map_dir_rollup",
        dir_path=dir_path,
        children=_render_children(children),
    )
    result = await client.complete(
        model=ROLLUP_MODEL,
        system=system,
        messages=[{"role": "user", "content": "Write the directory summary now."}],
        tools=None,
        max_tokens=budget,
        temperature=0.0,
    )
    return (result.text or "").strip()


async def dir_summary(client: APIClient, dir_path: str, child_summaries: list[ChildEntry]) -> str:
    """Roll up a directory's child *file* summaries into a ``DIR.md`` body."""
    return await _complete(client, dir_path=dir_path, children=child_summaries)


async def root_summary(client: APIClient, dir_summaries: list[ChildEntry]) -> str:
    """Roll up the repo's directory summaries into the ``ROOT.md`` body."""
    return await _complete(client, dir_path=".", children=dir_summaries)


__all__ = ["ChildEntry", "dir_summary", "root_summary"]
