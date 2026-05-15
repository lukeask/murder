"""Usage-aware schedule and queue view."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import yaml
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static, TextArea
from yaml import YAMLError


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


class ScheduleTicketsTable(DataTable):
    """Tickets on the critical path: status visible; carve only for planned."""

    BINDINGS = [
        Binding("enter", "request_carve", "Carve", show=False),
        Binding("c", "request_carve", "Carve", show=False),
    ]

    class CarveRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            self.ticket_id = ticket_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__(id="schedule_tickets", zebra_stripes=True, cursor_type="row")
        self._ids: list[str] = []
        self._statuses: list[str] = []

    def on_mount(self) -> None:
        self.add_columns("id", "wave", "status", "deps", "title")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        dep_subq = """
            NOT EXISTS (
                SELECT 1 FROM ticket_deps AS d
                  JOIN tickets AS dep ON dep.id = d.depends_on_id
                 WHERE d.ticket_id = t.id
                   AND dep.status != 'done'
            )
        """
        active = db.execute(
            f"""
            SELECT t.id, t.title, t.wave, t.status, {dep_subq} AS deps_ok
              FROM tickets AS t
             WHERE t.status IN ('planned', 'ready', 'in_progress', 'blocked', 'failed')
             ORDER BY
                   CASE t.status
                     WHEN 'planned' THEN 0
                     WHEN 'ready' THEN 1
                     WHEN 'in_progress' THEN 2
                     WHEN 'blocked' THEN 3
                     WHEN 'failed' THEN 4
                     ELSE 9
                   END,
                   t.wave, t.id
            """
        ).fetchall()
        recent_done = db.execute(
            f"""
            SELECT t.id, t.title, t.wave, t.status, {dep_subq} AS deps_ok
              FROM tickets AS t
             WHERE t.status = 'done'
             ORDER BY datetime(t.updated_at) DESC, t.id
             LIMIT 6
            """
        ).fetchall()

        self.clear()
        self._ids = []
        self._statuses = []
        for r in (*active, *recent_done):
            st = str(r["status"])
            if st == "planned":
                deps_cell = "ok" if int(r["deps_ok"]) else "wait"
            else:
                deps_cell = "—"
            self.add_row(r["id"], str(r["wave"]), st, deps_cell, r["title"])
            self._ids.append(r["id"])
            self._statuses.append(st)

    @property
    def cursor_ticket_id(self) -> str | None:
        if not self._ids:
            return None
        i = self.cursor_row
        if 0 <= i < len(self._ids):
            return self._ids[i]
        return None

    @property
    def cursor_is_planned(self) -> bool:
        i = self.cursor_row
        if not self._statuses or i < 0 or i >= len(self._statuses):
            return False
        return self._statuses[i] == "planned"

    def action_request_carve(self) -> None:
        tid = self.cursor_ticket_id
        if tid is None:
            return
        if not self.cursor_is_planned:
            self.app.notify(
                "Carve only applies to [b]planned[/b] tickets — pick a planned row.",
                severity="warning",
                timeout=5,
            )
            return
        self.post_message(self.CarveRequested(tid))


def _checklist_to_lines(snapshot: dict[str, Any]) -> str:
    items = snapshot.get("checklist") or []
    if not items:
        return ""
    rows = sorted(items, key=lambda x: int(x.get("ord", 0)))
    return "\n".join(str(x.get("text", "")).strip() for x in rows if str(x.get("text", "")).strip())


class CarveFormScreen(ModalScreen[dict[str, Any] | None]):
    """Structured carve / promote to ready — no raw YAML required."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    CSS = """
    CarveFormScreen {
        align: center middle;
    }
    #carve_dialog {
        width: 92;
        max-width: 98%;
        height: 90%;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }
    #carve_fields {
        height: 1fr;
        min-height: 10;
    }
    .field_label {
        margin-top: 1;
        text-style: bold;
    }
    #import_paste {
        height: 5;
        min-height: 3;
    }
    """

    def __init__(
        self,
        ticket_id: str,
        snapshot: dict[str, Any],
        *,
        harness_hint: str,
    ) -> None:
        super().__init__()
        self.ticket_id = ticket_id
        self._snapshot = snapshot
        self._harness_hint = harness_hint
        self._wave = int(snapshot.get("wave", 0))

    def compose(self) -> ComposeResult:
        with Vertical(id="carve_dialog"):
            yield Static(
                f"Promote [b]{self.ticket_id}[/b] to [b]ready[/b] — edit fields, or paste "
                "YAML/JSON from the collaborator and tap Merge.",
                id="carve_hdr",
            )
            yield Static(
                f"Current DB status: [b]{self._snapshot.get('status', '?')}[/b] — must be "
                "[b]planned[/b] to apply.",
                id="carve_status",
            )
            yield Static(f"Wave (fixed): {self._wave}", id="carve_wave")
            yield Static(self._harness_hint, id="carve_harness_hint")
            with Vertical(id="carve_fields"):
                yield Static("Title", classes="field_label")
                yield Input(placeholder="Title", id="field_title")
                yield Static("Harness (e.g. cursor)", classes="field_label")
                yield Input(placeholder="cursor", id="field_harness")
                yield Static("Model override (optional)", classes="field_label")
                yield Input(placeholder="Composer 2", id="field_model")
                yield Static("Deps — one ticket id per line", classes="field_label")
                yield TextArea(id="field_deps")
                yield Static("Write set — one repo-relative path per line", classes="field_label")
                yield TextArea(id="field_writes")
                yield Static("Skills — one per line (optional)", classes="field_label")
                yield TextArea(id="field_skills")
                yield Static("Checklist — one item per line", classes="field_label")
                yield TextArea(id="field_checklist")
            yield Static("Optional: paste collaborator YAML/JSON", classes="field_label")
            yield TextArea(id="import_paste")
            with Horizontal(id="carve_buttons"):
                yield Button("Merge paste → fields", id="merge")
                yield Button("Apply (planned → ready)", variant="primary", id="apply")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        snap = self._snapshot
        self.query_one("#field_title", Input).value = str(snap.get("title") or "")
        self.query_one("#field_harness", Input).value = str(snap.get("harness") or "")
        mod = snap.get("model")
        self.query_one("#field_model", Input).value = str(mod) if mod else ""
        deps = snap.get("deps") or []
        self.query_one("#field_deps", TextArea).text = "\n".join(str(d) for d in deps)
        writes = snap.get("write_set") or []
        self.query_one("#field_writes", TextArea).text = "\n".join(str(p) for p in writes)
        skills = snap.get("skills") or []
        self.query_one("#field_skills", TextArea).text = "\n".join(str(s) for s in skills)
        self.query_one("#field_checklist", TextArea).text = _checklist_to_lines(snap)
        self.query_one("#field_title", Input).focus()

    def _lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s:
                lines.append(s)
        return lines

    def _apply_import_to_form(self, data: dict[str, Any]) -> None:
        if title := data.get("title"):
            self.query_one("#field_title", Input).value = str(title).strip()
        ho = data.get("harness_override")
        h = ho if ho is not None else data.get("harness")
        if h:
            self.query_one("#field_harness", Input).value = str(h).strip()
        if data.get("model") is not None:
            self.query_one("#field_model", Input).value = str(data.get("model") or "").strip()
        if "deps" in data and data["deps"] is not None:
            deps = data["deps"]
            if isinstance(deps, list):
                self.query_one("#field_deps", TextArea).text = "\n".join(str(x) for x in deps)
        if "write_set" in data and data["write_set"] is not None:
            ws = data["write_set"]
            if isinstance(ws, list):
                self.query_one("#field_writes", TextArea).text = "\n".join(str(x) for x in ws)
        if "skills" in data and data["skills"] is not None:
            sk = data["skills"]
            if isinstance(sk, list):
                self.query_one("#field_skills", TextArea).text = "\n".join(str(x) for x in sk)
        if "checklist" in data and data["checklist"] is not None:
            ch = data["checklist"]
            if isinstance(ch, list):
                self.query_one("#field_checklist", TextArea).text = "\n".join(str(x) for x in ch)

    def _collect_spec(self) -> dict[str, Any]:
        title = self.query_one("#field_title", Input).value.strip()
        harness = self.query_one("#field_harness", Input).value.strip()
        model_raw = self.query_one("#field_model", Input).value.strip()
        return {
            "id": self.ticket_id,
            "title": title,
            "wave": self._wave,
            "harness_override": harness,
            "model": model_raw or None,
            "deps": self._lines(self.query_one("#field_deps", TextArea).text),
            "write_set": self._lines(self.query_one("#field_writes", TextArea).text),
            "skills": self._lines(self.query_one("#field_skills", TextArea).text),
            "checklist": self._lines(self.query_one("#field_checklist", TextArea).text),
        }

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "merge":
            raw = self.query_one("#import_paste", TextArea).text
            try:
                data = parse_carve_paste(raw)
            except (ValueError, json.JSONDecodeError, YAMLError) as e:
                self.app.notify(f"Could not parse paste: {e}", severity="error", timeout=8)
                return
            if str(data.get("id", self.ticket_id)) != self.ticket_id:
                self.app.notify(
                    f"Paste id {data.get('id')!r} does not match {self.ticket_id!r}",
                    severity="error",
                    timeout=8,
                )
                return
            wr = data.get("wave")
            if wr is not None and int(wr) != self._wave:
                self.app.notify(
                    f"Paste wave {wr} does not match ticket wave {self._wave} (ignored in form).",
                    severity="warning",
                    timeout=6,
                )
            self._apply_import_to_form(data)
            self.app.notify("Merged paste into fields.", timeout=3)
        elif event.button.id == "apply":
            spec = self._collect_spec()
            if not spec["title"]:
                self.app.notify("Title is required.", severity="warning", timeout=4)
                return
            if not str(spec.get("harness_override", "")).strip():
                self.app.notify("Harness is required.", severity="warning", timeout=4)
                return
            self.dismiss(spec)


class ScheduleView(Vertical):
    """Command-center: ticket roster + queue placeholder + usage."""

    DEFAULT_CSS = """
    ScheduleView {
        border: round $accent;
        height: 1fr;
        padding: 0 1;
    }
    ScheduleView #schedule_tickets {
        min-height: 6;
        max-height: 18;
        margin-bottom: 1;
    }
    ScheduleView #field_deps,
    ScheduleView #field_writes,
    ScheduleView #field_skills,
    ScheduleView #field_checklist {
        height: 4;
        min-height: 3;
    }
    ScheduleView #schedule_rest {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield ScheduleTicketsTable()
        yield Static("", id="schedule_rest")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        self.query_one(ScheduleTicketsTable).refresh_from_db(db)
        self.query_one("#schedule_rest", Static).update(_schedule_tail_content(db))

    @property
    def selected_ticket_id(self) -> str | None:
        return self.query_one(ScheduleTicketsTable).cursor_ticket_id

    @property
    def selected_ticket_is_planned(self) -> bool:
        return self.query_one(ScheduleTicketsTable).cursor_is_planned


def _schedule_tail_content(db: sqlite3.Connection | None) -> str:
    if db is None:
        return ""
    queued = db.execute(
        """
        SELECT id, ticket_id, title, harness, desired_start_at,
               max_usage_percent, status
          FROM schedule_queue
         WHERE status IN ('pending','scheduled','blocked')
         ORDER BY
               CASE WHEN desired_start_at IS NULL THEN 1 ELSE 0 END,
               desired_start_at,
               id
         LIMIT 10
        """
    ).fetchall()
    usage = db.execute(
        """
        SELECT s.harness, s.source, s.fetched_at, s.status_json
          FROM harness_usage_snapshots s
          JOIN (
                SELECT harness, MAX(fetched_at) AS fetched_at
                  FROM harness_usage_snapshots
                 GROUP BY harness
               ) latest
            ON latest.harness = s.harness
           AND latest.fetched_at = s.fetched_at
         ORDER BY s.harness
        """
    ).fetchall()

    lines = [
        "[b]Schedule[/b] — roster above shows status; [b]planned[/b] rows: [b]c[/b] / Enter "
        "opens promote form.",
        "",
        "[b]Scheduled / pending[/b]",
    ]
    if queued:
        for r in queued:
            start = _format_start(r["desired_start_at"])
            cap = (
                f" · start if usage <= {r['max_usage_percent']:.0f}%"
                if r["max_usage_percent"] is not None
                else ""
            )
            ticket = f"{r['ticket_id']} · " if r["ticket_id"] else ""
            lines.append(
                f"  #{r['id']} · {r['status']} · {start} · "
                f"{r['harness'] or 'default'}{cap} · {ticket}{r['title']}"
            )
    else:
        lines.append(
            "  (empty; scheduling not wired — use F6 or /murder to kick ready tickets)"
        )

    lines.extend(["", "[b]Latest usage windows[/b]"])
    if usage:
        for r in usage:
            lines.extend(_usage_lines(dict(r)))
    else:
        lines.append("  (no snapshots yet; press u to sample)")
        lines.append("  Probe tmux sessions: murder_<project>_usage_<harness>")

    return "\n".join(lines)


def _format_start(value: str | None) -> str:
    if not value:
        return "unscheduled"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%a %H:%M")


def _usage_lines(row: dict[str, Any]) -> list[str]:
    try:
        payload = json.loads(row["status_json"])
    except (TypeError, ValueError):
        return [f"  {row['harness']} · {row['source']} · malformed snapshot"]
    windows = payload.get("windows") or []
    if not isinstance(windows, list) or not windows:
        return [f"  {row['harness']} · {row['source']} · no windows"]
    out = [f"  {row['harness']} · {row['source']} · fetched {row['fetched_at']}"]
    for window in windows[:4]:
        if not isinstance(window, dict):
            continue
        name = window.get("name") or "usage"
        pct = window.get("percent_used")
        reset = window.get("reset_at") or window.get("ends_at") or "unknown reset"
        pct_text = f"{pct:.0f}%" if isinstance(pct, (int, float)) else "unknown"
        out.append(f"    {name}: {pct_text} used · reset {reset}")
    return out
