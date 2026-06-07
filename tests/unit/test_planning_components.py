"""Headless tests for planning-mode widgets as StoreComponents (t053).

All tests use pure stub infrastructure (no Textual App required) to verify:
- bind_stores + on_mount subscribes; on_unmount unsubscribes
- _render_from_stores reads the store snapshot and calls the render entrypoint
- document panes read bodies/selected_name from DocumentStoreSnapshot
- ChatLog reads turns from ConversationsStoreSnapshot via conversation_id
- Row derivation (name strip, status/rev/sync for plans; name/chars/updated for
  notes and reports) lives in the store snapshot, not the widget

Tests that exercise Markdown.update or DataTable require the Textual app context
and use asyncio.run + App.run_test().  Tests that only validate store-level
derivation are purely sync.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from murder.app.service.client_api import (
    NoteDisplaySnapshot,
    NotesSnapshot,
    NoteSummary,
    PlanDisplaySnapshot,
    PlansSnapshot,
    PlanSummary,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ReportSummary,
)
from murder.app.tui.components import StoreComponent
from murder.app.tui.stores.documents import (
    NotesStore,
    PlansStore,
    ReportsStore,
)
from murder.app.tui.stores.conversations import ConversationsStore, doc_to_chat_turns
from murder.app.service.client_api import (
    ConversationBlockSummary,
    ConversationSummary,
    ConversationsSnapshot,
)
from tests.support.factories import (
    factory_conversation_block,
    factory_conversation_summary,
    factory_conversations_snapshot,
    factory_note_summary,
    factory_notes_snapshot,
    factory_plan_summary,
    factory_plans_snapshot,
    factory_report_summary,
    factory_reports_snapshot,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_DT1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DT2 = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _plan(name: str = "plan-alpha", revision: int = 1, status: str = "active", sync: str = "clean") -> PlanSummary:
    return factory_plan_summary(name=name, revision=revision, status=status, sync=sync)


def _plans_snap(*plans: PlanSummary, key: str = "k1") -> PlansSnapshot:
    return factory_plans_snapshot(plans, invalidation_key=key, as_of=_DT1)


def _note(name: str = "note-a", char_count: int = 100, updated: datetime = _DT1) -> NoteSummary:
    return factory_note_summary(name=name, char_count=char_count, updated=updated)


def _notes_snap(*notes: NoteSummary, key: str = "k1") -> NotesSnapshot:
    return factory_notes_snapshot(notes, invalidation_key=key, as_of=_DT1)


def _report(name: str = "report-a", char_count: int = 200, updated: datetime = _DT1) -> ReportSummary:
    return factory_report_summary(name=name, char_count=char_count, updated=updated)


def _reports_snap(*reports: ReportSummary, key: str = "k1") -> ReportsSnapshot:
    return factory_reports_snapshot(reports, invalidation_key=key, as_of=_DT1)


def _block(
    ordinal: int,
    kind: str = "user",
    payload: dict | None = None,
    conversation_id: str = "conv-1",
) -> ConversationBlockSummary:
    return factory_conversation_block(
        ordinal, kind=kind, payload=payload, conversation_id=conversation_id
    )


def _conv_summary(
    conversation_id: str = "conv-1",
    agent_id: str = "agent-1",
    blocks: tuple[ConversationBlockSummary, ...] = (),
) -> ConversationSummary:
    return factory_conversation_summary(
        conversation_id=conversation_id, agent_id=agent_id, blocks=blocks
    )


def _convs_snapshot(*summaries: ConversationSummary) -> ConversationsSnapshot:
    return factory_conversations_snapshot(*summaries, as_of=_DT1)


# ---------------------------------------------------------------------------
# Store-level: row derivation lives in the DocumentStoreSnapshot
# ---------------------------------------------------------------------------


class TestPlansStoreRowDerivation:
    """PlansStore derives display rows in the snapshot, not in the widget."""

    def test_rows_present_after_ingest(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan("plan-alpha", revision=3, status="active", sync="clean")))
        snap = store.get_snapshot()
        assert len(snap.rows) == 1
        assert snap.rows[0] == ("alpha", "active", "3", "clean")

    def test_name_strip_removes_plan_prefix(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan("plan-my-feature")))
        assert store.get_snapshot().rows[0][0] == "my-feature"

    def test_name_without_prefix_unchanged(self) -> None:
        """Names that don't start with 'plan-' pass through unchanged."""
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan("orphan-doc")))
        assert store.get_snapshot().rows[0][0] == "orphan-doc"

    def test_rows_empty_on_no_items(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(key="k1"))
        assert store.get_snapshot().rows == ()

    def test_rows_updated_on_new_ingest(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan("plan-a", revision=1)))
        store.ingest_list(_plans_snap(_plan("plan-a", revision=2), key="k2"))
        assert store.get_snapshot().rows[0][2] == "2"


class TestNotesStoreRowDerivation:
    def test_rows_present_after_ingest(self) -> None:
        store = NotesStore(AsyncMock(return_value=None))
        store.ingest_list(_notes_snap(_note("meeting-2026", char_count=512, updated=_DT1)))
        snap = store.get_snapshot()
        assert len(snap.rows) == 1
        row = snap.rows[0]
        assert row[0] == "meeting-2026"
        assert row[1] == "512"
        # updated_at[:16] with T→space
        assert "T" not in row[2]
        assert row[2] == "2026-01-01 00:00"

    def test_rows_empty_on_no_items(self) -> None:
        store = NotesStore(AsyncMock(return_value=None))
        store.ingest_list(_notes_snap(key="k1"))
        assert store.get_snapshot().rows == ()


class TestReportsStoreRowDerivation:
    def test_rows_present_after_ingest(self) -> None:
        store = ReportsStore(AsyncMock(return_value=None))
        store.ingest_list(_reports_snap(_report("weekly-2026", char_count=1024, updated=_DT2)))
        snap = store.get_snapshot()
        row = snap.rows[0]
        assert row[0] == "weekly-2026"
        assert row[1] == "1024"
        assert row[2] == "2026-01-02 00:00"


# ---------------------------------------------------------------------------
# Store-level: conversations turns derivation
# ---------------------------------------------------------------------------


class TestDocToChatTurns:
    """doc_to_chat_turns is a headless helper in stores/conversations.py."""

    def test_user_segment(self) -> None:
        doc = {"segments": [{"type": "user", "text": "hello"}]}
        turns = doc_to_chat_turns(doc)
        assert turns == (("user", "hello"),)

    def test_assistant_segment(self) -> None:
        doc = {"segments": [{"type": "assistant", "text": "world"}]}
        turns = doc_to_chat_turns(doc)
        assert turns == (("assistant", "world"),)

    def test_empty_text_filtered(self) -> None:
        doc = {"segments": [{"type": "user", "text": "   "}]}
        turns = doc_to_chat_turns(doc)
        assert turns == ()

    def test_unknown_type_dropped(self) -> None:
        doc = {"segments": [{"type": "totally_unknown", "text": "x"}]}
        turns = doc_to_chat_turns(doc)
        assert turns == ()

    def test_tool_call_segment(self) -> None:
        doc = {"segments": [{"type": "tool_call", "title": "Bash", "input": "ls /"}]}
        turns = doc_to_chat_turns(doc)
        assert len(turns) == 1
        who, text = turns[0]
        assert who == "tool"
        assert "Bash" in text
        assert "ls /" in text

    def test_notice_with_severity(self) -> None:
        doc = {"segments": [{"type": "notice", "message": "oops", "severity": "warn"}]}
        turns = doc_to_chat_turns(doc)
        assert turns == (("notice", "warn: oops"),)

    def test_notice_without_severity(self) -> None:
        doc = {"segments": [{"type": "notice", "message": "info"}]}
        turns = doc_to_chat_turns(doc)
        assert turns == (("notice", "info"),)

    def test_no_segments_key(self) -> None:
        assert doc_to_chat_turns({}) == ()

    def test_returns_immutable_tuple(self) -> None:
        doc = {"segments": [{"type": "user", "text": "hi"}]}
        turns = doc_to_chat_turns(doc)
        assert isinstance(turns, tuple)

    def test_mixed_segments_multi_turn(self) -> None:
        doc = {
            "segments": [
                {"type": "user", "text": "q"},
                {"type": "assistant", "text": "a"},
                {"type": "user", "text": "q2"},
            ]
        }
        turns = doc_to_chat_turns(doc)
        assert len(turns) == 3
        assert turns[0] == ("user", "q")
        assert turns[1] == ("assistant", "a")
        assert turns[2] == ("user", "q2")


class TestConversationsStoreTurnsById:
    """turns_by_id is included in the snapshot after bootstrap/apply_event."""

    def test_turns_by_id_empty_on_bootstrap_empty(self) -> None:
        store = ConversationsStore()
        store.bootstrap(_convs_snapshot())
        assert store.get_snapshot().turns_by_id == ()

    def test_turns_by_id_populated_after_bootstrap(self) -> None:
        store = ConversationsStore()
        summary = _conv_summary(
            conversation_id="conv-1",
            blocks=(_block(0, payload={"type": "user", "text": "hi"}),),
        )
        store.bootstrap(_convs_snapshot(summary))
        snap = store.get_snapshot()
        assert len(snap.turns_by_id) == 1
        cid, turns = snap.turns_by_id[0]
        assert cid == "conv-1"
        assert ("user", "hi") in turns

    def test_turns_by_id_updated_after_apply_event(self) -> None:
        import types
        store = ConversationsStore()
        store.bootstrap(_convs_snapshot())
        event = types.SimpleNamespace(
            conversation_id="conv-2",
            block={
                "id": None,
                "ordinal": 0,
                "kind": "user",
                "payload": {"type": "assistant", "text": "response"},
                "sealed": True,
                "service_received_at": "2026-01-01T00:00:00",
            },
            agent_id="",
        )
        store.apply_event(event)
        snap = store.get_snapshot()
        assert any(cid == "conv-2" for cid, _ in snap.turns_by_id)


# ---------------------------------------------------------------------------
# Widget-level: StoreComponent subscription wiring (headless stubs)
# ---------------------------------------------------------------------------


class _StubPlanList(StoreComponent):
    """Minimal PlanList stub to test StoreComponent wiring without Textual."""

    def __init__(self) -> None:
        self.rendered_snapshots: list = []

    def _render_from_stores(self) -> None:
        bound = getattr(self, "_bound_stores", None)
        if not bound:
            return
        snap = list(bound.values())[0].get_snapshot()
        self.rendered_snapshots.append(snap)

    def refresh_from_snapshot(self, snap):  # type: ignore[no-untyped-def]
        self.rendered_snapshots.append(snap)


class TestPlanListStoreWiring:
    """Verify PlanList respects the StoreComponent contract (subscribe/unsub)."""

    def test_subscribe_on_mount(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        widget = _StubPlanList()
        widget.bind_stores(main=store)
        widget.on_mount()

        initial_count = len(widget.rendered_snapshots)
        store.ingest_list(_plans_snap(_plan(), key="k-new"))
        assert len(widget.rendered_snapshots) == initial_count + 1

    def test_initial_paint_on_mount(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan()))
        widget = _StubPlanList()
        widget.bind_stores(main=store)
        widget.on_mount()
        assert len(widget.rendered_snapshots) >= 1

    def test_unsubscribe_on_unmount(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        widget = _StubPlanList()
        widget.bind_stores(main=store)
        widget.on_mount()
        widget.on_unmount()
        widget.rendered_snapshots.clear()
        store.ingest_list(_plans_snap(_plan(), key="after-unmount"))
        assert widget.rendered_snapshots == []

    def test_noop_when_no_store_bound(self) -> None:
        widget = _StubPlanList()
        widget.on_mount()  # must not raise
        widget.on_unmount()  # must not raise
        assert widget.rendered_snapshots == []


# ---------------------------------------------------------------------------
# Store snapshot: rows content matches widget bridge path rows
# ---------------------------------------------------------------------------


class TestStoreRowsMatchBridgePath:
    """The rows derived by the store must produce the same values as the
    widget's own refresh_from_snapshot did before this refactor."""

    def test_plan_rows_match_bridge_derivation(self) -> None:
        """Store row == (name.removeprefix('plan-'), status, str(revision), sync)."""
        store = PlansStore(AsyncMock(return_value=None))
        p = _plan("plan-my-plan", revision=5, status="deprecated", sync="conflict")
        store.ingest_list(_plans_snap(p))
        row = store.get_snapshot().rows[0]
        assert row == ("my-plan", "deprecated", "5", "conflict")

    def test_note_rows_match_bridge_derivation(self) -> None:
        """Store row == (name, str(char_count), updated_at.isoformat()[:16] with T→space)."""
        store = NotesStore(AsyncMock(return_value=None))
        n = _note("2026-06-06", char_count=42, updated=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc))
        store.ingest_list(_notes_snap(n))
        row = store.get_snapshot().rows[0]
        assert row == ("2026-06-06", "42", "2026-06-06 14:30")

    def test_report_rows_match_bridge_derivation(self) -> None:
        """Store row == (name, str(char_count), updated_at.isoformat()[:16] with T→space)."""
        store = ReportsStore(AsyncMock(return_value=None))
        r = _report("weekly", char_count=999, updated=datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc))
        store.ingest_list(_reports_snap(r))
        row = store.get_snapshot().rows[0]
        assert row == ("weekly", "999", "2026-03-15 09:00")


# ---------------------------------------------------------------------------
# Document pane: bodies / selected_name accessible in snapshot
# ---------------------------------------------------------------------------


class TestDocumentPaneBodiesInSnapshot:
    """Verify the store exposes bodies and selected_name ready for document panes."""

    def test_body_available_after_request(self) -> None:
        loader = AsyncMock(return_value=PlanDisplaySnapshot(name="plan-x", markdown="# x"))
        store = PlansStore(loader)
        store.ingest_list(_plans_snap(_plan("plan-x")))
        asyncio.run(store.request_body("plan-x"))
        snap = store.get_snapshot()
        body_map = dict(snap.bodies)
        assert body_map.get("plan-x") == "# x"

    def test_selected_name_in_snapshot(self) -> None:
        store = PlansStore(AsyncMock(return_value=None))
        store.ingest_list(_plans_snap(_plan("plan-a")))
        store.set_selected("plan-a")
        snap = store.get_snapshot()
        assert snap.selected_name == "plan-a"

    def test_body_empty_when_not_loaded(self) -> None:
        store = NotesStore(AsyncMock(return_value=None))
        store.ingest_list(_notes_snap(_note("note-b")))
        store.set_selected("note-b")
        snap = store.get_snapshot()
        body_map = dict(snap.bodies)
        assert body_map.get("note-b") is None

    def test_note_body_available_after_request(self) -> None:
        loader = AsyncMock(return_value=NoteDisplaySnapshot(name="note-b", markdown="# nb"))
        store = NotesStore(loader)
        store.ingest_list(_notes_snap(_note("note-b")))
        asyncio.run(store.request_body("note-b"))
        body_map = dict(store.get_snapshot().bodies)
        assert body_map.get("note-b") == "# nb"

    def test_report_body_available_after_request(self) -> None:
        loader = AsyncMock(return_value=ReportDisplaySnapshot(name="report-c", markdown="# rc"))
        store = ReportsStore(loader)
        store.ingest_list(_reports_snap(_report("report-c")))
        asyncio.run(store.request_body("report-c"))
        body_map = dict(store.get_snapshot().bodies)
        assert body_map.get("report-c") == "# rc"


# ---------------------------------------------------------------------------
# ChatLog: store path renders turns from ConversationsStoreSnapshot
# ---------------------------------------------------------------------------


class _StubChatLog(StoreComponent):
    """Minimal ChatLog stub to verify the store path without Textual."""

    def __init__(self) -> None:
        self._conversation_id: str | None = None
        self.rendered_turns: list = []

    def _render_from_stores(self) -> None:
        bound = getattr(self, "_bound_stores", None)
        if not bound:
            return
        snap = list(bound.values())[0].get_snapshot()
        self.refresh_from_snapshot(snap)

    def refresh_from_snapshot(self, snapshot) -> None:  # type: ignore[no-untyped-def]
        cid = self._conversation_id
        if cid is None:
            return
        for conv_id, turns in snapshot.turns_by_id:
            if conv_id == cid:
                self.rendered_turns.append(turns)
                return

    def set_conversation_id(self, conversation_id: str | None) -> None:
        if self._conversation_id == conversation_id:
            return
        self._conversation_id = conversation_id
        bound = getattr(self, "_bound_stores", None)
        if bound:
            self._render_from_stores()


class TestChatLogStoreWiring:
    def test_subscribe_on_mount(self) -> None:
        store = ConversationsStore()
        store.bootstrap(_convs_snapshot())
        widget = _StubChatLog()
        widget.bind_stores(conversations=store)
        widget.set_conversation_id("conv-1")
        widget.on_mount()

        import types
        initial_count = len(widget.rendered_turns)
        event = types.SimpleNamespace(
            conversation_id="conv-1",
            block={
                "id": None, "ordinal": 0, "kind": "user",
                "payload": {"type": "user", "text": "new msg"},
                "sealed": True, "service_received_at": "2026-01-01T00:00:00",
            },
            agent_id="",
        )
        store.apply_event(event)
        assert len(widget.rendered_turns) > initial_count

    def test_unsubscribe_on_unmount(self) -> None:
        import types
        store = ConversationsStore()
        store.bootstrap(_convs_snapshot())
        widget = _StubChatLog()
        widget.bind_stores(conversations=store)
        widget.set_conversation_id("conv-1")
        widget.on_mount()
        widget.on_unmount()

        widget.rendered_turns.clear()
        event = types.SimpleNamespace(
            conversation_id="conv-1",
            block={
                "id": None, "ordinal": 0, "kind": "user",
                "payload": {"type": "user", "text": "after unmount"},
                "sealed": True, "service_received_at": "2026-01-01T00:00:00",
            },
            agent_id="",
        )
        store.apply_event(event)
        assert widget.rendered_turns == []

    def test_renders_correct_turns_for_conversation_id(self) -> None:
        store = ConversationsStore()
        s1 = _conv_summary(
            "conv-A",
            blocks=(_block(0, payload={"type": "user", "text": "q1"}),),
        )
        s2 = _conv_summary(
            "conv-B",
            blocks=(_block(0, payload={"type": "assistant", "text": "response"}),),
        )
        store.bootstrap(_convs_snapshot(s1, s2))

        widget = _StubChatLog()
        widget.bind_stores(conversations=store)
        widget.set_conversation_id("conv-A")
        widget.on_mount()
        widget.rendered_turns.clear()

        widget._render_from_stores()
        assert widget.rendered_turns
        turns_for_a = widget.rendered_turns[-1]
        assert any(t == ("user", "q1") for t in turns_for_a)
