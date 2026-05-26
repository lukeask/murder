"""Ticket roster and carve form for the Dispatch view."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

import yaml
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, SelectionList, Static, TextArea

from murder.harnesses import REGISTRY
from murder.tickets.status import TicketStatus

from murder.service.client_api import (
    ScheduleSnapshot,
    ScheduleTicketRow,
    TicketCarveSnapshot,
)
from murder.tui.dispatch.schedule_cells import (
    deps_cell_for,
    dispatch_schedule_cell,
    display_status_for,
)


def parse_carve_paste(text: str) -> dict[str, Any]:
    """Parse collaborator paste: JSON object or YAML mapping."""
    raw = text.strip()
    if not raw:
        raise ValueError("empty paste")
    if raw.startswith("{"):
        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("paste must be a JSON/YAML mapping")
    return data


def _checklist_to_lines(snapshot: dict[str, Any]) -> str:
    items = snapshot.get("checklist") or []
    if not items:
        return ""
    rows = sorted(items, key=lambda x: int(x.get("ord", 0)))
    return "\n".join(str(x.get("text", "")).strip() for x in rows if str(x.get("text", "")).strip())


class ScheduleTicketsTable(DataTable):
    """Tickets on the critical path with YAML metadata sync state visible."""

    BINDINGS = [
        Binding("enter", "request_carve", "Metadata", show=False),
        Binding("c", "request_carve", "Metadata", show=False),
        Binding("r", "retry_failed", "Retry (failed)", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    class CarveRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            self.ticket_id = ticket_id
            super().__init__()

    class RetryRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            self.ticket_id = ticket_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__(id="schedule_tickets", zebra_stripes=True, cursor_type="row")
        self._ids: list[str] = []
        self._statuses: list[str] = []

    def on_mount(self) -> None:
        # Fixed widths so a long title does not consume the whole viewport; other
        # columns stay visible (DataTable scrolls horizontally if total exceeds width).
        self.add_column("id", width=6)
        self.add_column("title", width=34)
        self.add_column("wave", width=5)
        self.add_column("status", width=14)
        self.add_column("deps", width=5)
        self.add_column("schedule", width=14)
        self.add_column("harness", width=14)
        self.add_column("model", width=18)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def refresh_from_snapshot(self, snapshot: ScheduleSnapshot) -> None:
        prev_ticket_id = self.cursor_ticket_id
        prev_row = self.cursor_row
        self.clear()
        self._ids = []
        self._statuses = []
        rows = (
            *snapshot.active_tickets,
            *snapshot.recent_done_tickets,
            *snapshot.archived_tickets,
        )
        for row in rows:
            self._append_row(snapshot, row)
        if self._ids:
            if prev_ticket_id is not None:
                try:
                    idx = self._ids.index(prev_ticket_id)
                except ValueError:
                    idx = min(max(0, prev_row), len(self._ids) - 1)
            else:
                idx = min(max(0, prev_row), len(self._ids) - 1)
            self.move_cursor(row=idx, animate=False)

    def _append_row(self, snapshot: ScheduleSnapshot, row: ScheduleTicketRow) -> None:
        sched = dispatch_schedule_cell(
            scheduler_mode=snapshot.scheduler_mode,
            row=row,
            decisions=snapshot.scheduler_decisions,
        )
        self.add_row(
            row.id,
            row.title,
            str(row.wave),
            display_status_for(row),
            deps_cell_for(row),
            sched,
            row.harness or "",
            row.model or "",
        )
        self._ids.append(row.id)
        self._statuses.append(row.status)

    @property
    def cursor_ticket_id(self) -> str | None:
        if not self._ids:
            return None
        i = self.cursor_row
        if 0 <= i < len(self._ids):
            return self._ids[i]
        return None

    @property
    def cursor_status(self) -> str | None:
        i = self.cursor_row
        if not self._statuses or i < 0 or i >= len(self._statuses):
            return None
        return self._statuses[i]

    @property
    def cursor_is_editable(self) -> bool:
        """True when a ticket row is selected (metadata carve may open for any status)."""
        return self.cursor_ticket_id is not None

    def action_request_carve(self) -> None:
        tid = self.cursor_ticket_id
        if tid is None:
            return
        self.post_message(self.CarveRequested(tid))

    def action_retry_failed(self) -> None:
        if self.cursor_status != "failed":
            self.app.notify(
                "Retry applies to [b]failed[/b] tickets only.",
                severity="warning",
                timeout=4,
            )
            return
        tid = self.cursor_ticket_id
        if tid is None:
            return
        self.post_message(self.RetryRequested(tid))


_SCHEDULE_NONE = "__murder_schedule_none__"

_STATUS_SELECT_OPTIONS: list[tuple[str, str]] = [
    ("Draft", "draft"),
    ("Planned", "planned"),
    ("Ready", "ready"),
    ("In progress", "in_progress"),
    ("Blocked", "blocked"),
    ("Failed", "failed"),
    ("Done", "done"),
    ("Archived", "archived"),
]

_HARNESS_SELECT_OPTIONS: list[tuple[str, str]] = [
    ("Cursor CLI", "cursor"),
    ("Claude Code", "claude_code"),
    ("Codex CLI", "codex"),
    ("Pi", "pi"),
    ("Native coding crow", "native_coding_crow"),
]


def _wave_select_options(wave_values: Sequence[int], current: int) -> list[tuple[str, int]]:
    waves = {int(w) for w in wave_values} | {int(current), 0}
    hi = max(waves)
    for w in range(hi + 1, hi + 8):
        waves.add(w)
    ordered = sorted(waves)[:48]
    return [(f"Wave {w}", w) for w in ordered]


def _schedule_select_options(snapshot_at: str | None) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = [("Not scheduled", _SCHEDULE_NONE)]
    now = datetime.now().astimezone()
    if snapshot_at:
        label = snapshot_at.replace("T", " ")[:19]
        opts.append((f"Keep current ({label})", snapshot_at))
    for hours, cap in ((1, "+1 hour"), (4, "+4 hours"), (24, "+24 hours")):
        opts.append((cap, (now + timedelta(hours=hours)).replace(microsecond=0).isoformat()))
    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    opts.append(("Tomorrow 09:00 (local)", tomorrow.isoformat()))
    return opts


def _model_select_options(harness: str, current_model: str | None) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = [("(no model override)", "")]
    cls = REGISTRY.get(harness)
    if cls is not None:
        for model_id, label in cls.available_startup_models:
            opts.append((f"{label} ({model_id})", model_id))
    if current_model and current_model not in {v for _, v in opts}:
        opts.append((f"Other: {current_model}", current_model))
    return opts


class RadioRow(Static):
    """Horizontal single-select field — options laid across the width.

    `h`/`l` (or left/right) move the selection; the marked option is the
    value. One focusable widget, so `j`/`k`/Tab still move between fields.
    Visual sibling of the settings screen's radio rows.
    """

    can_focus = True

    DEFAULT_CSS = """
    RadioRow {
        height: auto;
        padding: 0 1;
    }
    RadioRow:focus {
        background: $boost;
    }
    """

    BINDINGS = [
        Binding("left,h", "prev", "Prev option", show=False),
        Binding("right,l", "next", "Next option", show=False),
    ]

    class Changed(Message):
        """Posted when the selected option changes."""

        def __init__(self, radio: RadioRow) -> None:
            self.radio = radio
            super().__init__()

    def __init__(
        self,
        options: list[tuple[str, Any]],
        *,
        value: Any = None,
        id: str | None = None,
    ) -> None:
        # markup=False: bracket/paren glyphs in option labels must not be
        # parsed as Rich markup (mirrors settings_screen._SettingItem).
        super().__init__(id=id, markup=False)
        self._options: list[tuple[str, Any]] = list(options)
        self._selected: int = self._index_of(value) if value is not None else 0

    def _index_of(self, value: Any) -> int:
        for i, (_, v) in enumerate(self._options):
            if v == value:
                return i
        return 0

    def on_mount(self) -> None:
        self._render_options()

    @property
    def value(self) -> Any:
        if not self._options:
            return None
        return self._options[self._selected][1]

    @value.setter
    def value(self, new_value: Any) -> None:
        self._selected = self._index_of(new_value)
        self._render_options()

    def set_options(self, options: list[tuple[str, Any]], *, value: Any = None) -> None:
        """Replace the option list (e.g. when a dependent field changes)."""
        self._options = list(options)
        self._selected = self._index_of(value) if value is not None else 0
        self._render_options()

    def _render_options(self) -> None:
        cells = [
            f"{'(•)' if i == self._selected else '( )'} {label}"
            for i, (label, _) in enumerate(self._options)
        ]
        self.update("   ".join(cells) if cells else "( ) —")

    def action_prev(self) -> None:
        self._move(-1)

    def action_next(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        if len(self._options) < 2:
            return
        self._selected = (self._selected + delta) % len(self._options)
        self._render_options()
        self.post_message(self.Changed(self))


class TitleStripInput(Input):
    """Title row: j/k focus without typing; Enter edits; Escape leaves edit mode."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("select_on_focus", False)
        super().__init__(*args, **kwargs)
        self._editing = False

    @property
    def editing(self) -> bool:
        return self._editing

    def replace(self, text: str, start: int, end: int) -> None:
        if not self._editing:
            return
        super().replace(text, start, end)

    def check_consume_key(self, key: str, character: str | None) -> bool:
        """Browse mode: don't claim printable keys.

        Textual strips a screen/app binding from the binding chain if a
        more-focused widget's ``check_consume_key`` claims that key. An
        ``Input`` claims every printable key, which would swallow the carve
        form's ``j``/``k`` navigation. In browse mode we claim nothing so
        those bindings fire; in edit mode we behave like a normal Input.
        """
        if not self._editing:
            return False
        return super().check_consume_key(key, character)

    async def action_submit(self) -> None:
        if not self._editing:
            self._editing = True
            self.cursor_position = len(self.value)
            return
        await super().action_submit()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "escape" and self._editing:
            self._editing = False
            event.stop()
            event.prevent_default()
            return
        if event.is_printable and not self._editing:
            return
        await super()._on_key(event)

    def _on_paste(self, event: events.Paste) -> None:
        if not self._editing:
            return
        super()._on_paste(event)


class CarveTextArea(TextArea):
    """Multiline fields: read-only until Enter so j/k can move focus past the widget."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("show_cursor", True)
        super().__init__(*args, **kwargs)
        self._editing = False

    @property
    def editing(self) -> bool:
        return self._editing

    async def _on_key(self, event: events.Key) -> None:
        if not self._editing:
            if event.key == "enter":
                self._editing = True
                self.read_only = False
                event.stop()
                event.prevent_default()
            # All other keys bubble up to CarveFormScreen for j/k navigation
            return
        if event.key == "escape":
            self._editing = False
            self.read_only = True
            event.stop()
            event.prevent_default()
            return
        await super()._on_key(event)

    async def _on_paste(self, event: events.Paste) -> None:
        if self.read_only and not self._editing:
            return
        await super()._on_paste(event)


class CarveFormScreen(ModalScreen[None]):
    """Keyboard-friendly ticket metadata editor (lists + text fields)."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("j", "nav_next", "Next field", show=False, priority=True),
        Binding("k", "nav_prev", "Prev field", show=False, priority=True),
    ]

    CSS = """
    CarveFormScreen {
        align: center middle;
    }
    #carve_dialog {
        width: 90%;
        max-height: 92%;
        border: solid $primary;
        background: $surface;
    }
    #carve_title {
        background: $primary;
        color: $background;
        text-align: center;
        height: 1;
        padding: 0 2;
        text-style: bold;
    }
    #carve_subtitle {
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    #carve_fields {
        height: 1fr;
        min-height: 8;
        padding: 0 2;
    }
    #field_deps, #field_skills_pick {
        height: 9;
        min-height: 4;
        border: tall $surface;
    }
    #field_writes, #field_skills_extra, #field_checklist {
        height: 5;
        min-height: 3;
    }
    .field_label {
        margin-top: 1;
        text-style: bold;
    }
    #carve_help {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        carve: TicketCarveSnapshot,
        *,
        harness_hint: str,
        on_autosave: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__()
        self.ticket_id = carve.ticket_id
        self._carve = carve
        self._snapshot = dict(carve.fields)
        self._harness_hint = harness_hint
        self._on_autosave = on_autosave
        self._suppress_autosave = True

    def compose(self) -> ComposeResult:
        cur_status = str(self._snapshot.get("status", "planned"))
        with Vertical(id="carve_dialog"):
            yield Static("Edit ticket", id="carve_title")
            yield Static(
                f"id: [dim]{self.ticket_id}[/dim] · status: [b]{cur_status}[/b] · {self._harness_hint}",
                id="carve_subtitle",
            )
            with VerticalScroll(id="carve_fields"):
                yield Static("Status", classes="field_label")
                yield RadioRow(
                    _STATUS_SELECT_OPTIONS,
                    value=cur_status
                    if cur_status in {v for _, v in _STATUS_SELECT_OPTIONS}
                    else "planned",
                    id="field_status",
                )
                yield Static("Title", classes="field_label")
                yield TitleStripInput(placeholder="Title", id="field_title")
                yield Static("Wave", classes="field_label")
                yield RadioRow(
                    _wave_select_options(
                        self._carve.wave_options, int(self._snapshot.get("wave", 0))
                    ),
                    value=int(self._snapshot.get("wave", 0)),
                    id="field_wave",
                )
                yield Static("Scheduled start (optional)", classes="field_label")
                sched_opts = _schedule_select_options(
                    str(self._snapshot["schedule_at"])
                    if self._snapshot.get("schedule_at")
                    else None
                )
                sched_val = (
                    str(self._snapshot["schedule_at"])
                    if self._snapshot.get("schedule_at")
                    else _SCHEDULE_NONE
                )
                if sched_val != _SCHEDULE_NONE and sched_val not in {v for _, v in sched_opts}:
                    sched_opts.insert(1, (f"Keep ({sched_val[:19]}…)", sched_val))
                yield RadioRow(
                    sched_opts,
                    value=sched_val
                    if any(v == sched_val for _, v in sched_opts)
                    else _SCHEDULE_NONE,
                    id="field_schedule",
                )
                yield Static("Harness", classes="field_label")
                yield RadioRow(
                    list(_HARNESS_SELECT_OPTIONS),
                    value=str(self._snapshot.get("harness") or "cursor").strip() or "cursor",
                    id="field_harness",
                )
                yield Static("Model", classes="field_label")
                yield RadioRow([("(no model override)", "")], id="field_model")
                yield Static("Depends on (space toggles)", classes="field_label")
                yield SelectionList[str](id="field_deps")
                yield Static("Skills from project (space toggles)", classes="field_label")
                yield SelectionList[str](id="field_skills_pick")
                yield Static("Extra skills — one per line", classes="field_label")
                yield CarveTextArea(id="field_skills_extra")
                yield Static("Write set — one repo-relative path per line", classes="field_label")
                yield CarveTextArea(id="field_writes")
                yield Static("Checklist — one item per line", classes="field_label")
                yield CarveTextArea(id="field_checklist")
            yield Static(
                "Tab/Shift+Tab · j/k fields · Enter on title / multiline fields to edit · "
                "Esc closes (Esc exits edit)",
                id="carve_help",
            )

    def on_mount(self) -> None:
        self._suppress_autosave = True
        snap = self._snapshot
        self.query_one("#field_title", TitleStripInput).value = str(snap.get("title") or "")
        wave_row = self.query_one("#field_wave", RadioRow)
        wave_row.value = int(snap.get("wave", 0))

        harness_s = str(snap.get("harness") or "cursor").strip()
        h_row = self.query_one("#field_harness", RadioRow)
        hpairs = list(_HARNESS_SELECT_OPTIONS)
        known_kinds = {k for _, k in hpairs}
        if harness_s not in known_kinds:
            hpairs.append((f"Other: {harness_s}", harness_s))
        h_row.set_options(hpairs, value=harness_s)

        mod = str(snap.get("model") or "").strip() or None
        self._refresh_model_select(harness_s, mod)

        chosen = {str(d) for d in (snap.get("deps") or [])}
        deps_list = self.query_one("#field_deps", SelectionList)
        if self._carve.dependency_options:
            deps_list.add_options(
                [
                    (ref.title[:64], ref.id, ref.id in chosen)
                    for ref in self._carve.dependency_options
                ]
            )
        skills_pick = self.query_one("#field_skills_pick", SelectionList)
        cur_skills = {str(s) for s in (snap.get("skills") or [])}
        known = set(self._carve.known_skills)
        if self._carve.known_skills:
            skills_pick.add_options(
                [
                    (skill, skill, skill in cur_skills)
                    for skill in self._carve.known_skills
                ]
            )
        extra_skills = sorted(cur_skills - known)
        self.query_one("#field_skills_extra", CarveTextArea).text = "\n".join(extra_skills)

        writes = snap.get("write_set") or []
        self.query_one("#field_writes", CarveTextArea).text = "\n".join(str(p) for p in writes)
        self.query_one("#field_checklist", CarveTextArea).text = _checklist_to_lines(snap)
        self.query_one("#field_status", RadioRow).focus()
        self._suppress_autosave = False

    def _refresh_model_select(self, harness: str, prefer: str | None) -> None:
        m_row = self.query_one("#field_model", RadioRow)
        opts = _model_select_options(harness, prefer)
        cur = "" if prefer is None or prefer == "" else str(prefer).strip()
        m_row.set_options(opts, value=cur)

    def on_radio_row_changed(self, event: RadioRow.Changed) -> None:
        if event.radio.id == "field_harness":
            m_row = self.query_one("#field_model", RadioRow)
            cur = m_row.value
            cur_s = "" if cur in (None, "") else str(cur).strip()
            self._refresh_model_select(str(event.radio.value), cur_s or None)
        self._autosave()

    def _typing_focus(self) -> bool:
        w = self.focused
        if isinstance(w, TitleStripInput):
            return w.editing
        if isinstance(w, CarveTextArea):
            return w.editing
        return isinstance(w, TextArea)

    def action_nav_next(self) -> None:
        if self._typing_focus():
            return
        self.screen.focus_next()

    def action_nav_prev(self) -> None:
        if self._typing_focus():
            return
        self.screen.focus_previous()

    def _lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s:
                lines.append(s)
        return lines

    def _collect_spec(self) -> dict[str, Any]:
        title = self.query_one("#field_title", TitleStripInput).value.strip()
        harness = str(self.query_one("#field_harness", RadioRow).value)
        model_v = self.query_one("#field_model", RadioRow).value
        model_raw = "" if model_v in (None, "") else str(model_v).strip()
        wave = int(self.query_one("#field_wave", RadioRow).value)
        sched_v = self.query_one("#field_schedule", RadioRow).value
        if sched_v == _SCHEDULE_NONE:
            schedule_at: str | None = None
        else:
            schedule_at = str(sched_v)
        status_v = self.query_one("#field_status", RadioRow).value
        status = str(status_v) if status_v not in (None, "") else "planned"

        deps = list(self.query_one("#field_deps", SelectionList).selected)
        skills_sel = list(self.query_one("#field_skills_pick", SelectionList).selected)
        skills_extra = self._lines(self.query_one("#field_skills_extra", CarveTextArea).text)
        skills = sorted(set(skills_sel) | set(skills_extra))

        return {
            "id": self.ticket_id,
            "title": title,
            "wave": wave,
            "status": status,
            "harness": harness,
            "model": model_raw or None,
            "schedule_at": schedule_at,
            "deps": deps,
            "write_set": self._lines(self.query_one("#field_writes", CarveTextArea).text),
            "skills": skills,
            "checklist": self._lines(self.query_one("#field_checklist", CarveTextArea).text),
        }

    def _autosave(self) -> None:
        if self._suppress_autosave:
            return
        spec = self._collect_spec()
        if not spec["title"]:
            return
        if not str(spec.get("harness", "")).strip():
            return
        self._on_autosave(spec)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Changed, "#field_title")
    def _title_changed(self, _event: Input.Changed) -> None:
        self._autosave()

    @on(TextArea.Changed, "#field_skills_extra")
    @on(TextArea.Changed, "#field_writes")
    @on(TextArea.Changed, "#field_checklist")
    def _text_areas_changed(self, _event: TextArea.Changed) -> None:
        self._autosave()

    @on(SelectionList.SelectedChanged, "#field_deps")
    @on(SelectionList.SelectedChanged, "#field_skills_pick")
    def _selection_lists_changed(self, _event: SelectionList.SelectedChanged) -> None:
        self._autosave()
