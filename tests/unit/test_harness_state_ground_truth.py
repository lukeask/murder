"""Ground-truth pane-state tests derived from annotated tmux recordings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from murder.llm.harnesses import get as get_adapter
from murder.llm.harnesses.transcript_v2 import TranscriptAccumulator

FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_state"
HARNESSES = ("claude_code", "codex", "cursor")


def _rows(harness: str) -> list[dict]:
    path = FIXTURES / harness / "frames.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("harness", HARNESSES)
def test_adapter_idle_matches_annotated_state(harness: str) -> None:
    adapter = get_adapter(harness)
    for row in _rows(harness):
        assert adapter.is_idle(row["text"]) is row["idle"], (
            f"{harness} frame {row['frame']} expected {row['state']}"
        )


@pytest.mark.parametrize("harness", HARNESSES)
def test_transcript_v2_state_matches_annotated_state(harness: str) -> None:
    acc = TranscriptAccumulator(harness)
    for row in _rows(harness):
        acc.feed(row["text"])
        assert acc.to_dict()["state"] == row["state"], (
            f"{harness} frame {row['frame']} expected {row['state']}"
        )
