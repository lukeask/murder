"""Advanced flight-recorder substrate (Phase 2 of plan-advanced-logging-mode).

A separate, disposable SQLite "flight recorder" DB capturing the BULKY streams
(full API bodies, raw tmux frames, parsed-frame snapshots, …) for local
debugging. Fully independent of ``murder.db``: own connection, own
``schema_version``. Opt-in via the top rungs of the single ``--log-level`` knob
(``advanced`` → redacted, ``advanced-raw`` → unredacted); there is NO separate
flag and no second env var. The level resolver in
:mod:`murder.observability.logging_setup` maps the rung to the recorder mode.

Design contract:

- :class:`AdvancedLog` exposes typed ``record_*`` methods. The non-bus seams
  (api / tmux / parser / command / artifact / exception / state-mutation) each
  take a frozen record dataclass defined in THIS module — the payload contract
  lives in one place, so call sites construct a value instead of hand-building a
  dict whose key-shape only a future reader knows. OrchestrationNotifier-borne capture rides
  :meth:`AdvancedLog.record_bus_event`, which routes a published event to its
  ``record_family`` table (the bus is the single aspect; see the plan §2.5.A).
  Each method reads the four Phase 1 correlation ids from
  :mod:`murder.observability.log_context` ITSELF, stamps ``ts`` /
  ``capture_level``, applies :func:`redact` (unless raw mode), and ENQUEUES the
  row. A background asyncio task drains the queue with ``await queue.get()`` and
  batch-inserts. Writes are append-only and NON-BLOCKING: under backpressure a
  bounded queue drops + counts per family, and a ``gap_marker`` row is emitted so
  the shed records are visible rather than a silent hole.
- :class:`NullAdvancedLog` is the same interface, every method a no-op, no DB,
  no task. When the recorder is OFF this is what flows through the code paths so
  call sites stay UNCONDITIONAL.
- :func:`current_advanced_log` returns the writer set by ``Runtime.start`` via a
  module ContextVar, so the non-bus boundaries that have no direct Runtime handle
  can reach it without plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import dataclasses
import json
import logging
import re
import sqlite3
import subprocess
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from murder.observability.log_context import CONTEXT_FIELDS
from murder.state.storage.paths import advanced_log_path, advlogs_dir

LOG = logging.getLogger(__name__)

AdvancedMode = Literal["off", "redacted", "raw"]

SCHEMA_VERSION = 1
_QUEUE_MAXSIZE = 4096
_FLUSH_BATCH = 256

# Record families -> the cheap indexed extra columns each carries beyond the
# common (ts, correlation ids, capture_level, payload) set.
_FAMILY_EXTRA_COLUMNS: dict[str, tuple[str, ...]] = {
    "api_records": ("model",),
    "tmux_frames": ("session",),
    "parser_records": ("session",),
    "event_records": (),
    "command_records": (),
    "decision_records": (),
    "agent_records": (),
    "state_mutations": (),
    "artifact_refs": ("path",),
    "exception_records": ("site",),
}


# --------------------------------------------------------------------------- #
# Typed records (Step 2.2) — the payload contract for the non-bus seams lives
# here, in one module. Each ``record_*`` below takes one of these frozen values
# instead of loose kwargs / an ad-hoc dict, so a caller cannot typo a key-shape
# only a future reader knows. The ON-DISK json payload is unchanged: the writer
# decides which fields land in the row (e.g. ``dedup_hash`` gates but is not
# stored). OrchestrationNotifier-borne events are their own typed records (pydantic) and route via
# :meth:`AdvancedLog.record_bus_event`, so they need no dataclass here.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ApiRecord:
    """LLM/API ``complete()`` boundary: request + response bodies, usage."""

    request: Any = None
    response: Any = None
    model: str | None = None
    status: str | None = None
    retries: int | None = None
    usage: Any = None


@dataclass(frozen=True)
class TmuxFrameRecord:
    """A raw/sampled tmux pane frame. ``dedup_hash`` gates, it is not stored."""

    session: str
    op: str
    frame: Any = None
    meta: Any = None
    dedup_hash: str | None = None


@dataclass(frozen=True)
class ParserRecord:
    """A parsed-frame snapshot. ``dedup_hash`` gates, it is not stored."""

    session: str | None = None
    parsed: Any = None
    live_state: Any = None
    parse_error: Any = None
    choices: Any = None
    dedup_hash: str | None = None


@dataclass(frozen=True)
class CommandRecord:
    """A command-dispatch state transition (claim / complete / fail)."""

    phase: str
    command_id: str
    command: Any = None
    result: Any = None
    last_error: str | None = None
    retryable: bool | None = None


@dataclass(frozen=True)
class StateMutationRecord:
    """A persisted agent-field mutation at the sync choke point."""

    entity: str
    agent_id: str
    role: str | None = None
    ticket_id: str | None = None
    session: str | None = None
    status: str | None = None
    harness: str | None = None
    model: str | None = None
    worktree_path: str | None = None


@dataclass(frozen=True)
class ArtifactRefRecord:
    """A reference (never contents) to an existing large on-disk artifact."""

    path: str
    size: int | None = None
    sha: str | None = None
    line_range: Any = None
    byte_range: Any = None
    links: Any = None


@dataclass(frozen=True)
class ExceptionRecord:
    """A swallowed-exception site captured with full context."""

    site: str
    exc: Any
    payload: Any = None


def _event_envelope(event: Any) -> dict[str, Any]:
    """Serialize the FULL event envelope for the flight recorder.

    Unlike the Phase 1 ``events`` persist (which excludes the correlation /
    envelope fields), the recorder captures the bulky, complete envelope. Falls
    back to ``vars()`` for non-pydantic objects so capture never raises.
    """
    dump = getattr(event, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:  # pragma: no cover - capture must never crash publish
            pass
    try:
        return dict(vars(event))
    except TypeError:  # pragma: no cover - no __dict__
        return {"repr": repr(event)}


# --------------------------------------------------------------------------- #
# Module accessor (the non-bus seams reach the writer through this).
# --------------------------------------------------------------------------- #

_current: ContextVar[Optional["AdvancedLogBase"]] = ContextVar(
    "current_advanced_log", default=None
)


def set_current_advanced_log(log: "AdvancedLogBase") -> None:
    """Pin the process-wide advanced log (set once at ``Runtime.start``)."""
    _current.set(log)


def current_advanced_log() -> "AdvancedLogBase":
    """Return the active advanced log, or a shared no-op if none is set.

    Call sites that can't get the log injected call
    ``current_advanced_log().record_*(...)`` unconditionally; off-mode returns a
    :class:`NullAdvancedLog` so the call is a cheap no-op.
    """
    log = _current.get()
    return log if log is not None else _NULL_SINGLETON


# --------------------------------------------------------------------------- #
# Redaction (Step 2.7).
# --------------------------------------------------------------------------- #

_SECRET_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
)
_SECRET_HEADER_NAMES = frozenset({"authorization", "cookie", "set-cookie"})
# Substring match on signed-URL query params (case-insensitive).
_SIGNED_PARAM_SUBSTRINGS = ("x-amz-signature", "signature", "sig=", "token=", "x-goog-signature")
# Never redact these keys even if a substring rule would match.
_NEVER_REDACT_KEYS = frozenset(
    {"run_id", "agent_id", "command_id", "event_id", "ts", "model", "status", "retries"}
)

# Bearer tokens and OpenAI/Anthropic-style `sk-...` / long opaque keys in string
# VALUES.
_VALUE_SECRET_RE = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"  # Authorization: Bearer xxx
    r"|sk-[A-Za-z0-9._\-]{16,}"  # OpenAI-style secret key
    r"|sk-ant-[A-Za-z0-9._\-]{16,}"  # Anthropic-style key
)
_SIGNED_URL_RE = re.compile(
    r"(?i)([?&](?:x-amz-signature|signature|sig|token|x-goog-signature)=)[^&\s]+"
)


def _marker(field: str, reason: str) -> dict[str, Any]:
    return {"__redacted__": True, "field": field, "reason": reason}


def _key_is_secret(key: str) -> bool:
    low = key.lower()
    if low in _NEVER_REDACT_KEYS:
        return False
    if low in _SECRET_HEADER_NAMES:
        return True
    return any(sub in low for sub in _SECRET_KEY_SUBSTRINGS)


def _redact_string(value: str, *, field: str) -> Any:
    """Redact a leaf string value, preserving non-secret structure."""
    if _VALUE_SECRET_RE.search(value):
        return _marker(field, "secret-token-in-value")
    if _SIGNED_URL_RE.search(value):
        return _SIGNED_URL_RE.sub(lambda m: m.group(1) + "__redacted__", value)
    return value


def redact(obj: Any, *, _field: str = "") -> Any:
    """Deep-copy ``obj``, replacing secrets with a redaction marker.

    Replaces header keys ``Authorization`` / ``Cookie`` / ``Set-Cookie``;
    JSON fields named like ``api_key`` / ``token`` / ``secret`` / ``password`` /
    ``access_token`` / ``refresh_token`` (case-insensitive substring on key);
    bearer / ``sk-...`` tokens in string values; and signed-URL query params.
    NEVER redacts correlation ids, timestamps, model names, status codes, or
    retry metadata. Request/response STRUCTURE stays intact.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            key_s = str(key)
            if _key_is_secret(key_s):
                out[key_s] = _marker(key_s, "sensitive-key")
            else:
                out[key_s] = redact(val, _field=key_s)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item, _field=_field) for item in obj]
    if isinstance(obj, str):
        return _redact_string(obj, field=_field)
    return obj  # numbers, bools, None pass through untouched.


# --------------------------------------------------------------------------- #
# Noise control (Step 2.8).
# --------------------------------------------------------------------------- #


class ChangeGate:
    """Gate high-frequency streams to "changed since last accepted" + cadence.

    ``should_record`` returns ``False`` when ``content_hash`` is unchanged since
    the last accepted record for ``stream_key`` — UNLESS ``min_interval_s`` has
    elapsed since that record (the cadence override, ~1s for tmux frames). Every
    suppressed call is counted in :attr:`suppressed`.
    """

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._last_hash: dict[str, str] = {}
        self._last_ts: dict[str, float] = {}
        self.suppressed = 0

    def should_record(
        self,
        stream_key: str,
        content_hash: str,
        *,
        min_interval_s: float | None = None,
    ) -> bool:
        now = self._clock()
        prev_hash = self._last_hash.get(stream_key)
        unchanged = prev_hash is not None and prev_hash == content_hash
        if unchanged:
            if min_interval_s is None:
                self.suppressed += 1
                return False
            last_ts = self._last_ts.get(stream_key, float("-inf"))
            if now - last_ts < min_interval_s:
                self.suppressed += 1
                return False
        self._last_hash[stream_key] = content_hash
        self._last_ts[stream_key] = now
        return True


# --------------------------------------------------------------------------- #
# Schema.
# --------------------------------------------------------------------------- #


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    if conn.execute("SELECT 1 FROM schema_version").fetchone() is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_info (
            id          INTEGER PRIMARY KEY,
            ts          TEXT NOT NULL,
            run_id      TEXT,
            log_path    TEXT,
            mode        TEXT,
            flags_json  TEXT,
            config_hash TEXT,
            git_commit  TEXT,
            main_schema TEXT
        )
        """
    )
    for family, extras in _FAMILY_EXTRA_COLUMNS.items():
        extra_ddl = "".join(f"    {col} TEXT,\n" for col in extras)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {family} (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                run_id        TEXT,
                agent_id      TEXT,
                command_id    TEXT,
                event_id      TEXT,
                capture_level TEXT,
            {extra_ddl}    payload       TEXT
            )
            """
        )
        for col in extras:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{family}_{col} ON {family}({col})"
            )
    conn.commit()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # pragma: no cover - git absent / not a repo
        return None


# --------------------------------------------------------------------------- #
# Writer base + null implementation.
# --------------------------------------------------------------------------- #


class AdvancedLogBase:
    """Shared no-op surface so :class:`NullAdvancedLog` and the real writer share
    one interface. Every ``record_*`` is a no-op here; :class:`AdvancedLog`
    overrides the enqueue path."""

    mode: AdvancedMode = "off"

    async def start(self) -> None:  # pragma: no cover - trivial
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial
        return None

    async def aclose(self) -> None:
        await self.stop()

    def write_session_info(self, *, main_db: sqlite3.Connection | None = None) -> None:
        return None

    # -- record families -- #
    #
    # The bus is the single capture aspect: :meth:`record_bus_event` routes a
    # published event to its ``record_family`` table. The methods below are the
    # irreducible NON-BUS seams (plan §2.5.B), each taking a typed record.
    def record_bus_event(self, event: Any) -> None:
        return None

    def record_api(self, record: ApiRecord) -> None:
        return None

    def record_tmux_frame(self, record: TmuxFrameRecord) -> None:
        return None

    def record_parser(self, record: ParserRecord) -> None:
        return None

    def record_command(self, record: CommandRecord) -> None:
        return None

    def record_state_mutation(self, record: StateMutationRecord) -> None:
        return None

    def record_artifact_ref(self, record: ArtifactRefRecord) -> None:
        return None

    def record_exception(self, record: ExceptionRecord) -> None:
        return None


class NullAdvancedLog(AdvancedLogBase):
    """No-op writer used when advanced logging is off. No DB, no task."""

    mode: AdvancedMode = "off"


_NULL_SINGLETON = NullAdvancedLog()


class AdvancedLog(AdvancedLogBase):
    """Append-only, non-blocking SQLite flight recorder.

    Owns its own connection. ``record_*`` build a row, apply redaction (unless
    raw), and enqueue onto a bounded queue; a background task drains it. Safe to
    call ``record_*`` before :meth:`start` (the row is buffered in the queue) and
    after :meth:`stop` (no-op).
    """

    def __init__(self, db_path: Path, *, mode: AdvancedMode, run_id: str | None) -> None:
        self.mode = mode
        self.run_id = run_id
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        _create_schema(self._conn)
        self._queue: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        # Per-family count of records shed under backpressure since the last
        # gap_marker row for that family. A non-zero value triggers a gap_marker
        # on the next successful enqueue so the hole is visible, never silent.
        self._drops: dict[str, int] = {}
        self.gate = ChangeGate()

    @property
    def dropped(self) -> int:
        """Total records shed under backpressure across all families."""
        return sum(self._drops.values())

    # -- lifecycle -- #

    async def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            # Sentinel wakes the blocked `await queue.get()` so the loop exits
            # after draining. But if the queue is FULL the sentinel is shed —
            # cancel is the guaranteed terminator. This is safe: `_write_batch`
            # has no await, so cancellation can only fire at the `queue.get()`
            # BETWEEN batches, never mid-write, and `_flush_remaining` below
            # picks up anything still queued. (Without the cancel a full queue at
            # shutdown deadlocks stop() forever.)
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(("__stop__", ()))
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._flush_remaining()
        with contextlib.suppress(Exception):
            self._conn.commit()
            self._conn.close()

    async def _drain_loop(self) -> None:
        while True:
            family, row = await self._queue.get()
            if family == "__stop__":
                return
            batch: list[tuple[str, tuple]] = [(family, row)]
            # Opportunistically coalesce whatever else is already queued.
            while len(batch) < _FLUSH_BATCH:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item[0] == "__stop__":
                    self._write_batch(batch)
                    return
                batch.append(item)
            self._write_batch(batch)

    def _write_batch(self, batch: list[tuple[str, tuple]]) -> None:
        try:
            for family, row in batch:
                self._insert(family, row)
            self._conn.commit()
        except Exception:  # pragma: no cover - capture must never crash runtime
            LOG.debug("advanced-log batch write failed", exc_info=True)

    def _flush_remaining(self) -> None:
        batch: list[tuple[str, tuple]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item[0] == "__stop__":
                continue
            batch.append(item)
        if batch:
            self._write_batch(batch)

    # -- enqueue core -- #

    def _insert(self, family: str, row: tuple) -> None:
        extras = _FAMILY_EXTRA_COLUMNS[family]
        cols = ["ts", *CONTEXT_FIELDS, "capture_level", *extras, "payload"]
        placeholders = ", ".join("?" for _ in cols)
        self._conn.execute(
            f"INSERT INTO {family} ({', '.join(cols)}) VALUES ({placeholders})", row
        )

    def _build_row(self, payload: Any, extras: tuple) -> tuple:
        from murder.observability import log_context as _lc

        ids = tuple(_lc._VARS[name].get() for name in CONTEXT_FIELDS)
        body = payload if self.mode == "raw" else redact(payload)
        try:
            payload_json = json.dumps(body, default=str)
        except Exception:  # pragma: no cover
            payload_json = json.dumps({"__unserializable__": True})
        ts = datetime.now(timezone.utc).isoformat()
        return (ts, *ids, self.mode, *extras, payload_json)

    def _enqueue(self, family: str, payload: Any, *, extras: tuple = ()) -> None:
        if self._closed:
            return
        row = self._build_row(payload, extras)
        try:
            self._queue.put_nowait((family, row))
        except asyncio.QueueFull:
            # Shed + count rather than stall the runtime. Visibility comes from
            # the gap_marker emitted on the next record that DOES fit.
            self._drops[family] = self._drops.get(family, 0) + 1
            return
        pending = self._drops.get(family, 0)
        if pending:
            self._emit_gap_marker(family, pending, extras)

    def _emit_gap_marker(self, family: str, dropped: int, extras: tuple) -> None:
        """Enqueue a visible 'N records lost here' row (best-effort).

        Reuses the family's own table (same column arity, blank extras) so a
        reader sees the gap inline with the surviving rows. If the queue is still
        full the marker is deferred — the drop count is retained until it lands.
        """
        marker = self._build_row(
            {"__gap_marker__": True, "family": family, "dropped_since_last": dropped},
            ("",) * len(_FAMILY_EXTRA_COLUMNS[family]),
        )
        try:
            self._queue.put_nowait((family, marker))
        except asyncio.QueueFull:  # pragma: no cover - still saturated; retry later
            return
        self._drops[family] = 0

    # -- session_info -- #

    def write_session_info(self, *, main_db: sqlite3.Connection | None = None) -> None:
        from murder.state.persistence.migrations import current_schema_marker

        main_schema = ""
        config_hash = ""
        if main_db is not None:
            with contextlib.suppress(Exception):
                main_schema = current_schema_marker(main_db)
            with contextlib.suppress(Exception):
                row = main_db.execute(
                    "SELECT config_snapshot FROM runs WHERE run_id = ?", (self.run_id,)
                ).fetchone()
                if row is not None:
                    import hashlib

                    config_hash = hashlib.sha256(
                        str(row["config_snapshot"]).encode("utf-8")
                    ).hexdigest()
        flags = {"recorder_mode": self.mode}
        self._conn.execute(
            """
            INSERT INTO session_info
                (ts, run_id, log_path, mode, flags_json, config_hash, git_commit, main_schema)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                self.run_id,
                str(self._db_path),
                self.mode,
                json.dumps(flags),
                config_hash,
                _git_commit(),
                main_schema,
            ),
        )
        self._conn.commit()

    # -- bus aspect: route a published event to its family table -- #

    def record_bus_event(self, event: Any) -> None:
        """Capture a published orchestration event into its typed record family.

        The recorder is registered as a bus SUBSCRIBER (plan §2.5.A); this is
        its handler body. The destination table is the event class's
        ``record_family`` classvar (default ``event_records``); ``None`` opts the
        event out of capture entirely. Runs inside the publisher's
        ``log_context`` (``asyncio.gather`` copies it into the handler task), so
        the correlation ids are read by :meth:`_build_row` as usual.
        """
        family = getattr(type(event), "record_family", "event_records")
        if family is None:
            return
        self._enqueue(family, _event_envelope(event))

    # -- record families: the irreducible non-bus seams (plan §2.5.B) -- #

    def record_api(self, record: ApiRecord) -> None:
        payload = {
            "request": record.request,
            "response": record.response,
            "model": record.model,
            "status": record.status,
            "retries": record.retries,
            "usage": record.usage,
        }
        self._enqueue("api_records", payload, extras=(record.model,))

    def record_tmux_frame(self, record: TmuxFrameRecord) -> None:
        if record.dedup_hash is not None and not self.gate.should_record(
            f"tmux:{record.session}:{record.op}", record.dedup_hash, min_interval_s=1.0
        ):
            return
        payload = {
            "session": record.session,
            "op": record.op,
            "frame": record.frame,
            "meta": record.meta,
        }
        self._enqueue("tmux_frames", payload, extras=(record.session,))

    def record_parser(self, record: ParserRecord) -> None:
        if record.dedup_hash is not None and not self.gate.should_record(
            f"parser:{record.session}", record.dedup_hash
        ):
            return
        payload = {
            "session": record.session,
            "parsed": record.parsed,
            "live_state": record.live_state,
            "parse_error": record.parse_error,
            "choices": record.choices,
        }
        self._enqueue("parser_records", payload, extras=(record.session,))

    def record_command(self, record: CommandRecord) -> None:
        # phase / command_id are required fields, so the drop-None filter never
        # sheds them; the other fields are optional context.
        payload = {k: v for k, v in dataclasses.asdict(record).items() if v is not None}
        self._enqueue("command_records", payload)

    def record_state_mutation(self, record: StateMutationRecord) -> None:
        self._enqueue("state_mutations", dataclasses.asdict(record))

    def record_artifact_ref(self, record: ArtifactRefRecord) -> None:
        self._enqueue(
            "artifact_refs", dataclasses.asdict(record), extras=(str(record.path),)
        )

    def record_exception(self, record: ExceptionRecord) -> None:
        exc = record.exc
        exc_text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        body = {"site": record.site, "exc": exc_text, "payload": record.payload}
        self._enqueue("exception_records", body, extras=(record.site,))


# --------------------------------------------------------------------------- #
# Mode resolution + factory.
# --------------------------------------------------------------------------- #


def open_advanced_log(
    repo_root: Path, run_id: str, mode: AdvancedMode
) -> AdvancedLogBase:
    """Return a :class:`NullAdvancedLog` when ``mode=="off"``, else an
    :class:`AdvancedLog` over a fresh per-session DB under ``advlogs_dir``.

    The caller is responsible for ``await log.start()`` (and later
    ``await log.stop()``).
    """
    if mode == "off":
        return NullAdvancedLog()
    advlogs_dir(repo_root).mkdir(parents=True, exist_ok=True)
    db_path = advanced_log_path(repo_root, run_id, raw=(mode == "raw"))
    return AdvancedLog(db_path, mode=mode, run_id=run_id)
