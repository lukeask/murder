"""Checklist protocol (D6).

Checklist items live in the `checklist` SQLite table (not in markdown).
Monkey interacts via pane-output protocol:

    >>> CHECK: <exact item text>
    >>> ASK: <question>
    >>> NOTE: <text>
    >>> DONE

This module owns the parse → DB-update plumbing. Augur's tick loop calls
`apply_pane_protocol(pane_text, ticket_id)` on every poll.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from murder.harnesses.base import HarnessAdapter


def apply_pane_protocol(
    conn: sqlite3.Connection,
    ticket_id: str,
    pane_text: str,
    harness: HarnessAdapter,
    notes_path: Path,
) -> dict[str, list[str]]:
    """Parse all D6 tokens from `pane_text`; update DB + notes file as side effects.

    Returns {'checked': [...], 'asks': [...], 'notes': [...], 'done': [bool-as-list]}
    so the caller can decide what events to emit (mostly QuestionEvent on asks).
    """
    # TODO(M4): for each in harness.detect_checks(pane): db.check_off_item(...)
    #          for each in harness.detect_notes(pane): tickets.parser.append_section(notes_path, 'Working notes', body)
    #          asks: caller emits QuestionEvent (this module just returns)
    #          done: caller (orchestrator.on_monkey_done) handles the post-hoc git diff.
    raise NotImplementedError("M4: checklist.apply_pane_protocol")


def deduplicate(seen: Iterable[str], current: list[str]) -> list[str]:
    """Filter `current` against `seen`, returning only new items.

    Augur runs this between ticks so a `>>> CHECK:` that lingers in the
    pane buffer for several captures isn't double-counted.
    """
    s = set(seen)
    return [x for x in current if x not in s]
