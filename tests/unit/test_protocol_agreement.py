"""Cross-language drift guard for the bus wire contract (A-D1).

`murder/bus/protocol.py` is the source of truth for the JSON-RPC-over-Unix-socket
bus; `inktui/src/bus/protocol.ts` is its hand-maintained TypeScript port. The two
halves build against their own copy and never import each other, so they can
silently drift — exactly the failure that let `NoteEvent` / ``type "note"`` go
missing from the TS union for a while.

This test pins the contract from the **Python side** and asserts the TS file
agrees on the three things that matter for dispatch correctness:

  - ``PROTOCOL_VERSION`` (both must be 4; a client refuses a mismatched server).
  - the ``Entity`` enum value *set* (both directions — Python⊆TS and TS⊆Python —
    so adding or dropping a value on either side fails).
  - the ``BusEvent`` member *type names*, including ``NoteEvent`` / ``type "note"``
    by name (the specific regression this test exists to prevent).

The TS side is read as text and scraped with regexes — no Node, no tsc. The Python
side reflects over the real module. Drift on either side breaks this test; fix by
editing whichever file is wrong so both sides match again.

Sibling of ``tests/unit/test_conversation_block_golden.py``, which anchors the one
content-bearing block shape; this one anchors the enum/version/event-name surface.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from murder.bus import protocol as pyproto
from murder.bus.protocol import BusEvent, Entity

# Repo root = three levels up from this file (tests/unit/<file>).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_PROTOCOL = _REPO_ROOT / "inktui" / "src" / "bus" / "protocol.ts"


@pytest.fixture(scope="module")
def ts_source() -> str:
    assert _TS_PROTOCOL.is_file(), f"TS protocol not found at {_TS_PROTOCOL}"
    return _TS_PROTOCOL.read_text(encoding="utf-8")


def _ts_protocol_version(src: str) -> int:
    m = re.search(r"export\s+const\s+PROTOCOL_VERSION\s*=\s*(\d+)\s*;", src)
    assert m, "could not find `export const PROTOCOL_VERSION = N;` in protocol.ts"
    return int(m.group(1))


def _ts_entity_values(src: str) -> set[str]:
    # `export type Entity = 'ticket' | 'agent' | ... | 'queue_row';` — may span lines.
    m = re.search(r"export\s+type\s+Entity\s*=\s*([^;]+);", src, re.DOTALL)
    assert m, "could not find `export type Entity = ...;` in protocol.ts"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def _ts_bus_event_members(src: str) -> set[str]:
    # `export type BusEvent = | HeartbeatEvent | SummaryEvent | ... ;` — spans lines.
    m = re.search(r"export\s+type\s+BusEvent\s*=\s*([^;]+);", src, re.DOTALL)
    assert m, "could not find `export type BusEvent = ...;` in protocol.ts"
    return set(re.findall(r"\b([A-Z]\w+Event)\b", m.group(1)))


def _py_entity_values() -> set[str]:
    return {e.value for e in Entity}


def _py_bus_event_members() -> set[str]:
    # BusEvent is an Annotated discriminated union; its members carry __name__.
    import typing

    args = typing.get_args(BusEvent)
    # The discriminated-union Annotated wrapper puts the Union first.
    union = args[0] if args else BusEvent
    return {member.__name__ for member in typing.get_args(union)}


def _py_bus_event_type_literals() -> set[str]:
    """The `type` discriminant string of every BusEvent member."""
    import typing

    args = typing.get_args(BusEvent)
    union = args[0] if args else BusEvent
    literals: set[str] = set()
    for member in typing.get_args(union):
        type_field = member.model_fields.get("type")
        assert type_field is not None, f"{member.__name__} has no `type` field"
        (literal_value,) = typing.get_args(type_field.annotation)
        literals.add(literal_value)
    return literals


def test_protocol_version_agrees(ts_source: str) -> None:
    assert pyproto.PROTOCOL_VERSION == 4, "Python PROTOCOL_VERSION drifted from expected 4"
    assert _ts_protocol_version(ts_source) == pyproto.PROTOCOL_VERSION, (
        "PROTOCOL_VERSION mismatch between protocol.py and protocol.ts"
    )


def test_entity_enum_value_set_agrees(ts_source: str) -> None:
    py = _py_entity_values()
    ts = _ts_entity_values(ts_source)
    assert len(py) == 8, f"expected 8 Entity values in Python, got {sorted(py)}"
    # Both directions so drift either way fails.
    assert py == ts, f"Entity value drift: only-in-py={py - ts}, only-in-ts={ts - py}"


def test_bus_event_member_names_agree(ts_source: str) -> None:
    py = _py_bus_event_members()
    ts = _ts_bus_event_members(ts_source)
    assert py == ts, f"BusEvent member drift: only-in-py={py - ts}, only-in-ts={ts - py}"


def test_note_event_present_both_sides(ts_source: str) -> None:
    # The specific regression this test exists to prevent: NoteEvent / type "note".
    assert "NoteEvent" in _py_bus_event_members(), "NoteEvent missing from Python BusEvent"
    assert "note" in _py_bus_event_type_literals(), "type \"note\" missing from Python BusEvent"
    assert "NoteEvent" in _ts_bus_event_members(ts_source), "NoteEvent missing from TS BusEvent"
    assert re.search(
        r"type:\s*'note'", ts_source
    ), "`type: 'note'` discriminant missing from protocol.ts"
