"""Roster store — CrowSnapshot → projected CrowEntry list.

Poll-fed store that ingests :class:`~murder.app.service.client_api.CrowSnapshot`
and emits an immutable tuple of projected :class:`CrowEntry` objects.  No
Textual imports; safe to unit-test headlessly.

Data shaping extracted from crows_view.py so any future crow visualisation
reads the same projected entries rather than re-deriving them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from murder.app.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.app.tui.crow_health import Health, classify, is_stuck
from murder.app.tui.stores.base import BaseStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TERMINAL_AGENT_STATUSES = frozenset({"done", "dead"})
"""Agent states excluded from the wall."""

TERMINAL_TICKET_STATUSES = frozenset({"done", "failed"})
"""Ticket states that indicate the work item is closed."""

FAILED_STALE_AFTER = timedelta(hours=2)
"""Hide failed agents after this long without a recent heartbeat."""

_STATUS_SORT_RANK = {
    "escalating": 0,
    "blocked": 1,
    "running": 2,
    "idle": 3,
    "failed": 4,
}

_CROW_PREFIX_RE = re.compile(r"^murder_[^_]+_crow_")
_KNOWN_HARNESS_ALIASES = {
    "agv",
    "antigrav",
    "antigravity",
    "claude",
    "claude_code",
    "codex",
    "cursor",
    "pi",
}

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrowEntry:
    """One tile in the wall, projected from :class:`CrowSessionSummary`."""

    agent_id: str
    ticket_id: str
    ticket_title: str | None
    harness: str
    status: str
    session: str | None
    health: Health
    started_at: datetime | None = None
    model: str | None = None


@dataclass(frozen=True)
class CrowDisplayLabels:
    """Compact UI labels for one crow across roster and tile views."""

    name: str
    harness: str
    model: str
    is_rogue: bool


@dataclass(frozen=True, slots=True)
class RosterSnapshot:
    """Immutable snapshot emitted by :class:`RosterStore`."""

    entries: tuple[CrowEntry, ...]
    invalidation_key: str


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _short_display_name(raw: str) -> str:
    """Strip project/template prefix, yielding harness+role+id."""
    m = _CROW_PREFIX_RE.match(raw)
    return raw[m.end():] if m else raw


def _display_harness(raw: str) -> str:
    kind = raw.strip().lower()
    return {
        "antigrav": "agv",
        "antigravity": "agv",
        "claude": "claude",
        "claude_code": "claude",
        "codex": "codex",
        "cursor": "cursor",
        "pi": "pi",
    }.get(kind, kind or "—")


def _compact_model(raw: str | None, *, limit: int = 18) -> str:
    model = str(raw or "").strip()
    if not model:
        return "—"
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    if len(model) <= limit:
        return model
    return model[: limit - 1] + "…"


def _display_name(raw: str, harness: str = "") -> str:
    short = _short_display_name(raw).strip()
    if not short:
        return "crow"
    for marker in ("_rogue_", "-rogue-"):
        if marker in short:
            _prefix, suffix = short.split(marker, 1)
            return suffix or short
    for prefix in ("rogue_", "rogue-"):
        if short.startswith(prefix):
            short = short[len(prefix):]
            break
    harness_aliases = {
        harness.strip().lower(),
        _display_harness(harness),
    } | _KNOWN_HARNESS_ALIASES
    for alias in sorted((a for a in harness_aliases if a), key=len, reverse=True):
        for sep in ("_", "-"):
            token = f"{alias}{sep}"
            if short.startswith(token):
                trimmed = short[len(token):]
                if trimmed:
                    return trimmed
    return short


def _is_rogue_entry(entry: CrowEntry) -> bool:
    for raw in (entry.session, entry.agent_id):
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if "_rogue_" in text or "-rogue-" in text or text.startswith(("rogue_", "rogue-")):
            return True
    return False


def _crow_display_labels(entry: CrowEntry) -> CrowDisplayLabels:
    raw_name = entry.session or entry.agent_id or ""
    return CrowDisplayLabels(
        name=_display_name(raw_name, entry.harness) or "crow",
        harness=_display_harness(entry.harness),
        model=_compact_model(entry.model),
        is_rogue=_is_rogue_entry(entry),
    )


def crow_title_label(entry: CrowEntry) -> str:
    labels = _crow_display_labels(entry)
    parts = [labels.name, labels.harness]
    if labels.model != "—":
        parts.append(labels.model)
    if labels.is_rogue:
        parts.append("rogue")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Snapshot projection
# ---------------------------------------------------------------------------


def _health_for_summary(session: CrowSessionSummary, *, now: datetime) -> Health:
    return classify(
        status=session.status,
        open_escalations=session.open_escalations,
        max_severity=session.max_severity,
        stuck=is_stuck(status=session.status, last_seen=session.last_seen, now=now),
    )


def _keep_failed_session(session: CrowSessionSummary, *, now: datetime) -> bool:
    if session.status != "failed":
        return True
    ticket_status = session.ticket_status or ""
    if ticket_status and ticket_status not in TERMINAL_TICKET_STATUSES:
        return True
    last_seen = session.last_seen or session.started_at
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    else:
        last_seen = last_seen.astimezone(timezone.utc)
    return now - last_seen <= FAILED_STALE_AFTER


def _entry_from_session(
    session: CrowSessionSummary,
    *,
    now: datetime,
) -> CrowEntry | None:
    if session.role not in {"crow", "rogue"}:
        return None
    status = session.status
    if status in TERMINAL_AGENT_STATUSES:
        return None
    if status == "failed" and not _keep_failed_session(session, now=now):
        return None
    tile_id = session.agent_id or session.session_name or session.ticket_id or ""
    if not tile_id:
        return None
    title = session.ticket_title or session.harness or session.ticket_id or tile_id
    return CrowEntry(
        agent_id=tile_id,
        ticket_id=session.ticket_id or "",
        ticket_title=title,
        harness=session.harness or "",
        status=status,
        session=session.session_name,
        health=_health_for_summary(session, now=now),
        started_at=session.started_at,
        model=session.model,
    )


def entries_from_snapshot(
    snapshot: CrowSnapshot,
    *,
    now: datetime | None = None,
) -> list[CrowEntry]:
    """Project snapshot sessions into wall entries, filtered and sorted."""
    now = now or datetime.now(timezone.utc)
    entries: list[CrowEntry] = []
    for session in snapshot.sessions:
        entry = _entry_from_session(session, now=now)
        if entry is not None:
            entries.append(entry)
    entries.sort(
        key=lambda e: (
            _STATUS_SORT_RANK.get(e.status, 99),
            e.ticket_id or "",
            e.agent_id,
        )
    )
    return entries


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class RosterStore(BaseStore[RosterSnapshot]):
    """Poll-fed store for the crow roster.

    Call :meth:`ingest_snapshot` each poll tick; subscribers are only notified
    when the projected entry set changes (health flip, new/removed crow, etc.).
    Identical projected output — same entries tuple and invalidation_key —
    produces no notification.
    """

    def __init__(self) -> None:
        super().__init__(RosterSnapshot(entries=(), invalidation_key=""))

    def ingest_snapshot(
        self,
        snapshot: CrowSnapshot,
        *,
        now: datetime | None = None,
    ) -> None:
        """Project and store; no-op when the result is identical."""
        entries = tuple(entries_from_snapshot(snapshot, now=now))
        self._set(RosterSnapshot(entries=entries, invalidation_key=snapshot.invalidation_key))
