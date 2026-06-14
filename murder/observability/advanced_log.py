"""Advanced flight-recorder substrate (Phase 2 of plan-advanced-logging-mode).

A separate, disposable SQLite "flight recorder" DB capturing the BULKY streams
(full API bodies, raw tmux frames, parsed-frame snapshots, …) for local
debugging. Fully independent of ``murder.db``: own connection, own
``schema_version``. Opt-in via ``--advanced-logging`` / ``--advanced-logging-raw``
(env vars ``MURDER_ADVANCED_LOGGING`` / ``MURDER_ADVANCED_LOGGING_RAW``).

Design contract (Wave 4 depends on it):

- :class:`AdvancedLog` exposes typed ``record_*`` methods, one per record family.
  Each reads the four Phase 1 correlation ids from
  :mod:`murder.observability.log_context` ITSELF, stamps ``ts`` /
  ``capture_level``, applies :func:`redact` (unless raw mode), and ENQUEUES the
  row. A background asyncio task drains the queue with ``await queue.get()`` and
  batch-inserts. Writes are append-only and NON-BLOCKING: under backpressure a
  bounded queue drops + counts rather than stalling the runtime.
- :class:`NullAdvancedLog` is the same interface, every method a no-op, no DB,
  no task. When the flags are OFF this is what flows through the code paths so
  Wave 4 call sites stay UNCONDITIONAL.
- :func:`current_advanced_log` returns the writer set by ``Runtime.start`` via a
  module ContextVar, so the 7 instrumentation boundaries that have no direct
  Runtime handle can reach it without plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import re
import sqlite3
import subprocess
import time
from contextvars import ContextVar
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
# Module accessor (Wave 4 reaches the writer through this).
# --------------------------------------------------------------------------- #

_current: ContextVar[Optional["AdvancedLogBase"]] = ContextVar(
    "current_advanced_log", default=None
)


def set_current_advanced_log(log: "AdvancedLogBase") -> None:
    """Pin the process-wide advanced log (set once at ``Runtime.start``)."""
    _current.set(log)


def current_advanced_log() -> "AdvancedLogBase":
    """Return the active advanced log, or a shared no-op if none is set.

    Wave 4 boundaries that cannot reach the Runtime call
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

    # -- record families (Wave 4 contract) -- #
    def record_api(self, *, request=None, response=None, model=None, status=None,
                   retries=None, usage=None) -> None:
        return None

    def record_tmux_frame(self, *, session, op, frame=None, meta=None, dedup_hash=None) -> None:
        return None

    def record_parser(self, *, session=None, parsed=None, live_state=None,
                      parse_error=None, choices=None, dedup_hash=None) -> None:
        return None

    def record_event(self, *, payload) -> None:
        return None

    def record_command(self, *, payload) -> None:
        return None

    def record_decision(self, *, payload) -> None:
        return None

    def record_agent(self, *, payload) -> None:
        return None

    def record_state_mutation(self, *, payload) -> None:
        return None

    def record_artifact_ref(self, *, path, size=None, sha=None, line_range=None,
                            byte_range=None, links=None) -> None:
        return None

    def record_exception(self, *, site, exc, payload=None) -> None:
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
        self.dropped = 0
        self.gate = ChangeGate()

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
            # after draining, rather than being cancelled mid-batch.
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(("__stop__", ()))
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

    def _enqueue(self, family: str, payload: Any, *, extras: tuple = ()) -> None:
        if self._closed:
            return
        from murder.observability import log_context as _lc

        ids = tuple(_lc._VARS[name].get() for name in CONTEXT_FIELDS)
        body = payload if self.mode == "raw" else redact(payload)
        try:
            payload_json = json.dumps(body, default=str)
        except Exception:  # pragma: no cover
            payload_json = json.dumps({"__unserializable__": True})
        ts = datetime.now(timezone.utc).isoformat()
        row = (ts, *ids, self.mode, *extras, payload_json)
        try:
            self._queue.put_nowait((family, row))
        except asyncio.QueueFull:
            self.dropped += 1

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
        flags = {
            "advanced_logging": self.mode in ("redacted", "raw"),
            "advanced_logging_raw": self.mode == "raw",
        }
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

    # -- record families (Wave 4 contract) -- #

    def record_api(self, *, request=None, response=None, model=None, status=None,
                   retries=None, usage=None) -> None:
        payload = {
            "request": request,
            "response": response,
            "model": model,
            "status": status,
            "retries": retries,
            "usage": usage,
        }
        self._enqueue("api_records", payload, extras=(model,))

    def record_tmux_frame(self, *, session, op, frame=None, meta=None, dedup_hash=None) -> None:
        if dedup_hash is not None and not self.gate.should_record(
            f"tmux:{session}:{op}", dedup_hash, min_interval_s=1.0
        ):
            return
        payload = {"session": session, "op": op, "frame": frame, "meta": meta}
        self._enqueue("tmux_frames", payload, extras=(session,))

    def record_parser(self, *, session=None, parsed=None, live_state=None,
                      parse_error=None, choices=None, dedup_hash=None) -> None:
        if dedup_hash is not None and not self.gate.should_record(
            f"parser:{session}", dedup_hash
        ):
            return
        payload = {
            "session": session,
            "parsed": parsed,
            "live_state": live_state,
            "parse_error": parse_error,
            "choices": choices,
        }
        self._enqueue("parser_records", payload, extras=(session,))

    def record_event(self, *, payload) -> None:
        self._enqueue("event_records", payload)

    def record_command(self, *, payload) -> None:
        self._enqueue("command_records", payload)

    def record_decision(self, *, payload) -> None:
        self._enqueue("decision_records", payload)

    def record_agent(self, *, payload) -> None:
        self._enqueue("agent_records", payload)

    def record_state_mutation(self, *, payload) -> None:
        self._enqueue("state_mutations", payload)

    def record_artifact_ref(self, *, path, size=None, sha=None, line_range=None,
                            byte_range=None, links=None) -> None:
        payload = {
            "path": path,
            "size": size,
            "sha": sha,
            "line_range": line_range,
            "byte_range": byte_range,
            "links": links,
        }
        self._enqueue("artifact_refs", payload, extras=(str(path),))

    def record_exception(self, *, site, exc, payload=None) -> None:
        exc_text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
        body = {"site": site, "exc": exc_text, "payload": payload}
        self._enqueue("exception_records", body, extras=(site,))


# --------------------------------------------------------------------------- #
# Mode resolution + factory.
# --------------------------------------------------------------------------- #


def _env_true(name: str) -> bool:
    return (__import__("os").environ.get(name, "") or "").strip().lower() in ("1", "true", "yes")


def resolve_advanced_mode() -> AdvancedMode:
    """Resolve the advanced-logging mode from env vars (raw wins, raw implies on).

    ``MURDER_ADVANCED_LOGGING_RAW`` → ``"raw"``; else ``MURDER_ADVANCED_LOGGING``
    → ``"redacted"``; else ``"off"``.
    """
    if _env_true("MURDER_ADVANCED_LOGGING_RAW"):
        return "raw"
    if _env_true("MURDER_ADVANCED_LOGGING"):
        return "redacted"
    return "off"


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
