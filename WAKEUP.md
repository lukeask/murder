# WAKEUP — t056: Layout module + bridge removal

Branch: `tui-component-library`

## What this refactor did

Replaced the "bridge" pattern (app.py subscribed callbacks that called widget render methods directly) with the **StoreComponent self-subscribe pattern**: each top-level widget subscribes to its store on mount and renders independently when the store notifies.

## New files / packages

```
murder/app/tui/layout/__init__.py          — exports DefaultLayout
murder/app/tui/layout/default_layout.py   — core: instantiates + binds all widgets
murder/app/tui/stores/                     — pre-existing; unchanged by this ticket
```

## What migrated (self-subscribe path)

| Widget | Store(s) | Notes |
|---|---|---|
| Header | dispatch, roster, schedule | Multi-store; overrides `_render_from_stores()` |
| TicketGrid | dispatch | Single-store |
| CrowsView | roster | Single-store |
| PlanList | plans | Single-store |
| NotesList | notes | Single-store |
| ReportsList | reports | Single-store |
| PlanDocument | plans | Reads `selected_name` + `bodies` from snapshot |
| NotesDocument | notes | Reads `selected_name` + `bodies` from snapshot |
| ReportDocument | reports | Reads `selected_name` + `bodies` from snapshot |
| DispatchView | schedule | Cascades snapshot to children |
| EscalationStrip | escalations | Single-store |

## What stayed on pane-tick / ad-hoc paths (and why)

### PaneMirror — pane-tick only
`PaneMirror` is an async capture-pane consumer that calls `capture_pane()` on each
`coordinator.pane_tick()` call. The session it captures changes dynamically (crow
selection, plan selection). A TailStore would require a feeder in `coordinator.pane_tick`
that produces a per-session snapshot — plausible but adds complexity for little gain.
**Phase 3 follow-up.**

### ChatLog (collab\_chat) — ad-hoc path
The planning chat conversation_id switches at runtime between `"planner-{name}"` and
`"collaborator-0"` based on which planner target is active. The render also includes
status placeholders ("no planner session yet", "nothing parsed yet") that are not
modelled in the store snapshot. `app.py` drives `collab_chat` via `set_turns()` /
`replace_transcript()` using `conversations.doc_for()` directly.
**Phase 3 follow-up:** lift status strings into the store, then call `set_conversation_id()`
and let the widget self-subscribe.

### DispatchView children — parent-cascade pattern
`ModeStrip`, `GaugeStrip`, `ScheduleTicketsTable`, `CalendarPanel` are composed inside
`DispatchView` and receive the schedule snapshot via `DispatchView.refresh_from_snapshot`
cascade. They are unbound `StoreComponent` subclasses — this is an intentional parent-
cascade design, not a migration gap. `DispatchView` is the single schedule-store subscriber.

## Coordination callbacks retained in app.py

These are **app-level side effects**, not widget renders:

| Callback | What it does |
|---|---|
| `_on_roster_changed` | Refreshes conversation docs for all crow tiles |
| `_on_plans_changed` | Resyncs open plan doc + chat routing on plans list change |
| `_on_notes_changed` | Resyncs open note doc on notes list change |
| `_on_reports_changed` | Resyncs open report doc on reports list change |

`last_crow_snapshot` is retained on `IngestionCoordinator` for three helpers that need
raw `CrowSnapshot.sessions` (planner chat-target cycling, collaborator mirror session
lookup, crow-session-for-ticket lookup) — these roles are filtered out of
`RosterSnapshot.entries` by `entries_from_snapshot`.

## Bridge methods deleted from app.py

All seven `_bridge_*` methods removed:
- `_bridge_dispatch`, `_bridge_roster`, `_bridge_schedule`
- `_bridge_plans`, `_bridge_notes`, `_bridge_reports`, `_bridge_escalations`

## Mixin changes (StoreComponent)

- **Idempotency guard**: `on_mount` returns early if `_unsubs` is already set.
  Prevents double-subscription when a subclass calls `super().on_mount()` AND
  Textual's own MRO dispatch also invokes `StoreComponent.on_mount`.
- **Binding contract**: no-store path is a no-op (correct for cascade children and
  the ad-hoc `ChatLog` path). Docstring now distinguishes intentional patterns from
  migration gaps.

## Store API additions

`_DocumentStore.invalidate_body(name)`: evicts cached body so next `request_body()`
re-fetches. Called in `_open_plan`, `_open_note`, `_open_report` after user edits.

## Test status

```
828 passed, 1 failed (pre-existing)
```

The 1 failure is `test_full_doc_matches_expected[cc]` in `test_transcript.py` — a fixture
mismatch predating this ticket, unrelated to the TUI refactor.

## How to run

```bash
# From worktree root
cd /home/user/Documents/code/murder/.murder/worktrees/tui-component-library

# Run tests
python -m pytest tests/unit -q

# Run the app (requires a live murder runtime + Node >= 20)
python -m murder
```

## Judgment calls + open risks

1. **`inject_gauge_drill_in_loader`** swallows all exceptions on the `try/except` block.
   If `query_one(GaugeStrip)` isn't mounted at app `on_mount`, gauge drill-in silently
   fails. Low risk in practice since compose/mount order is deterministic. Consider
   replacing bare `except` with a logged warning.

2. **Perf spans gone**: the `tui.header.refresh_counts`, `tui.grid.refresh`,
   `tui.schedule.refresh` spans are gone with the bridge callbacks. Observability
   degraded slightly; add them to the store-change callbacks if needed.

3. **Plan/Note/Report doc rendering**: the store path (`refresh_from_snapshot` reads
   `selected_name` + `bodies`) replaces the old `set_plan_markdown()`/`show()` async
   methods. The async methods are kept as they may be called from tests or external code.

4. **`_plan_revision_map` and `_plan_content_cache`**: removed from app.py. The document
   stores now handle version-keyed body caching internally. If edge cases appear
   (e.g., stale body after concurrent edits), call `invalidate_body()` before
   `request_body()`.
