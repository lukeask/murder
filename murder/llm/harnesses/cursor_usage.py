"""Cursor usage API client.

Cursor does not expose usage through the interactive agent CLI. This module
reads a local Cursor auth token (agent CLI ``auth.json`` and/or IDE
``state.vscdb``) and calls Cursor's current-period usage API.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from murder.llm.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.usage import utc_now_iso

LOGGER = logging.getLogger(__name__)

# Cursor retired POST /auth/token; OAuth refresh lives at /oauth/token.
AUTH_URL = "https://api2.cursor.sh/oauth/token"
# Public Cursor IDE OAuth client id (used by the desktop app for refresh).
CURSOR_OAUTH_CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5


class CursorUsageError(Exception):
    """Base exception for Cursor usage collection failures."""


class CursorNotInstalledError(CursorUsageError):
    """No local Cursor auth store was found."""


class CursorNotAuthenticatedError(CursorUsageError):
    """No usable Cursor auth token was found."""


class CursorAPIError(CursorUsageError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class _TokenPair:
    access: str | None
    refresh: str | None
    source: str


def _db_path() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        )
    return os.path.expanduser("~/.config/Cursor/User/globalStorage/state.vscdb")


def _agent_auth_path() -> str:
    # Cursor Agent CLI login store (distinct from the desktop IDE state.vscdb).
    return os.path.expanduser("~/.config/cursor/auth.json")


def _read_db_keys(*keys: str) -> dict[str, str]:
    path = _db_path()
    if not os.path.exists(path):
        raise CursorNotInstalledError(f"Cursor DB not found at {path}")

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT key, value FROM ItemTable WHERE key IN ({placeholders})",
            keys,
        )
        return {str(row[0]): str(row[1]) for row in rows}
    finally:
        conn.close()


def _read_agent_auth() -> _TokenPair | None:
    path = _agent_auth_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        LOGGER.warning("could not read Cursor agent auth at %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("accessToken") or data.get("access_token")
    refresh = data.get("refreshToken") or data.get("refresh_token")
    access_s = str(access) if access else None
    refresh_s = str(refresh) if refresh else None
    if not access_s and not refresh_s:
        return None
    return _TokenPair(access=access_s, refresh=refresh_s, source="agent-auth.json")


def _read_ide_auth() -> _TokenPair | None:
    path = _db_path()
    if not os.path.exists(path):
        return None
    try:
        keys = _read_db_keys("cursorAuth/accessToken", "cursorAuth/refreshToken")
    except Exception:
        LOGGER.warning("could not read Cursor IDE auth at %s", path, exc_info=True)
        return None
    access = keys.get("cursorAuth/accessToken")
    refresh = keys.get("cursorAuth/refreshToken")
    if not access and not refresh:
        return None
    return _TokenPair(access=access, refresh=refresh, source="ide-state.vscdb")


def _load_token_pairs() -> list[_TokenPair]:
    pairs: list[_TokenPair] = []
    for loader in (_read_agent_auth, _read_ide_auth):
        pair = loader()
        if pair is not None:
            pairs.append(pair)
    return pairs


def _jwt_exp(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data["exp"])
    except Exception:
        # Malformed token → treat as expired so a refresh is attempted.
        LOGGER.debug("could not decode Cursor JWT exp; treating token as expired")
        return 0


def _refresh_token(refresh_token: str) -> str | None:
    request = urllib.request.Request(
        AUTH_URL,
        data=json.dumps(
            {
                "grant_type": "refresh_token",
                "client_id": CURSOR_OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500]
        LOGGER.warning(
            "Cursor token refresh failed: HTTP %s. body=%s. Re-auth may be required",
            exc.code,
            body,
            exc_info=True,
        )
        return None
    except Exception:
        LOGGER.warning("Cursor token refresh failed. Re-auth may be required", exc_info=True)
        return None

    value = data.get("access_token") or data.get("accessToken")
    if value:
        return str(value)
    if data.get("shouldLogout"):
        raise CursorNotAuthenticatedError(
            "Cursor refresh rejected session. Re-authenticate Cursor"
        )
    LOGGER.warning("Cursor token refresh returned no access_token")
    return None


def get_access_token(*, force_refresh: bool = False) -> str:
    pairs = _load_token_pairs()
    if not pairs:
        if not os.path.exists(_db_path()) and not os.path.exists(_agent_auth_path()):
            raise CursorNotInstalledError(
                f"No Cursor auth store found (checked {_agent_auth_path()} and {_db_path()})"
            )
        raise CursorNotAuthenticatedError("No Cursor auth tokens found")

    # Prefer the freshest access token that is still usable.
    if not force_refresh:
        valid = [
            (pair, _jwt_exp(pair.access))
            for pair in pairs
            if pair.access and _jwt_exp(pair.access) > time.time() + 60
        ]
        if valid:
            valid.sort(key=lambda item: item[1], reverse=True)
            chosen, _exp = valid[0]
            LOGGER.debug("using Cursor access token from %s", chosen.source)
            return chosen.access  # type: ignore[return-value]

    # Try refresh tokens from freshest store first; skip dead sessions.
    ordered = sorted(
        pairs,
        key=lambda pair: max(
            _jwt_exp(pair.access) if pair.access else 0,
            _jwt_exp(pair.refresh) if pair.refresh else 0,
        ),
        reverse=True,
    )
    rejected_session = False
    for pair in ordered:
        if not pair.refresh:
            continue
        try:
            new_token = _refresh_token(pair.refresh)
        except CursorNotAuthenticatedError:
            rejected_session = True
            LOGGER.info("Cursor refresh rejected session from %s", pair.source)
            continue
        if new_token:
            LOGGER.debug("refreshed Cursor access token via %s", pair.source)
            return new_token

    # Refresh failed or unavailable. An expired access token would only earn a
    # 401 (surfaced later as a generic API error), so flag the auth problem here
    # where it is actionable ("re-auth Cursor") rather than masking it.
    if not force_refresh:
        soft = [
            (pair, _jwt_exp(pair.access))
            for pair in pairs
            if pair.access and _jwt_exp(pair.access) > time.time()
        ]
        if soft:
            soft.sort(key=lambda item: item[1], reverse=True)
            LOGGER.warning(
                "Cursor access token unrefreshed but not yet expired. Using %s",
                soft[0][0].source,
            )
            return soft[0][0].access  # type: ignore[return-value]

    if rejected_session:
        raise CursorNotAuthenticatedError(
            "Cursor refresh rejected session. Re-authenticate Cursor"
        )
    raise CursorNotAuthenticatedError(
        "Cursor access token expired and could not be refreshed. Re-authenticate Cursor"
    )


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            return result, pos
    return result, pos


def _decode_proto(data: bytes) -> dict[int, list[int | float | bytes]]:
    fields: dict[int, list[int | float | bytes]] = {}
    pos = 0
    while pos < len(data):
        tag_wire, pos = _decode_varint(data, pos)
        field_num = tag_wire >> 3
        wire_type = tag_wire & 0x7
        if wire_type == WIRE_VARINT:
            value, pos = _decode_varint(data, pos)
        elif wire_type == WIRE_LENGTH_DELIMITED:
            length, pos = _decode_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
        elif wire_type == WIRE_FIXED64:
            value = struct.unpack_from("<d", data, pos)[0]
            pos += 8
        elif wire_type == WIRE_FIXED32:
            value = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        else:
            break
        fields.setdefault(field_num, []).append(value)
    return fields


def _fetch_raw(token: str) -> bytes:
    request = urllib.request.Request(
        USAGE_URL,
        data=b"",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/proto",
            "Connect-Protocol-Version": "1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            message = str(json.loads(body).get("message", body))
        except Exception:
            message = body[:200]
        raise CursorAPIError(exc.code, message) from exc


def _first_int(fields: dict[int, list[int | float | bytes]], key: int) -> int | None:
    return next((value for value in fields.get(key, []) if isinstance(value, int)), None)


def _first_float(fields: dict[int, list[int | float | bytes]], key: int) -> float | None:
    return next((value for value in fields.get(key, []) if isinstance(value, float)), None)


def _strings(fields: dict[int, list[int | float | bytes]], key: int) -> list[str]:
    values: list[str] = []
    for value in fields.get(key, []):
        if isinstance(value, bytes):
            values.append(value.decode(errors="replace"))
    return values


def _ms_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo("UTC")).isoformat()


def _status_from_proto(raw: bytes) -> HarnessUsageStatus:
    top = _decode_proto(raw)
    inner_raw = top.get(3, [b""])[0]
    inner = _decode_proto(inner_raw) if isinstance(inner_raw, bytes) else {}

    plan_inner_raw = top.get(4, [b""])[0]
    plan_inner = _decode_proto(plan_inner_raw) if isinstance(plan_inner_raw, bytes) else {}
    plan_raw = plan_inner.get(8, [b""])[0]
    plan = plan_raw.decode(errors="replace") if isinstance(plan_raw, bytes) else None

    period_start_ms = _first_int(top, 1)
    period_end_ms = _first_int(top, 2)
    auto_used = _first_int(inner, 1)
    auto_limit = _first_int(inner, 2)
    api_used = _first_int(inner, 3)
    api_limit = _first_int(inner, 5)

    # Fields 12/13 carry the consumed-percentage for the auto-composer and API
    # quotas directly. `used`/`limit` are raw request counts for display only —
    # they are NOT a clean used-of-limit pair, so percent comes from the field.
    windows: list[HarnessUsageWindow] = []
    if (pct_auto := _first_float(inner, 12)) is not None:
        windows.append(
            HarnessUsageWindow(
                name="auto_composer",
                percent_used=pct_auto,
                starts_at=_ms_iso(period_start_ms),
                ends_at=_ms_iso(period_end_ms),
                reset_at=_ms_iso(period_end_ms),
                used=auto_used,
                limit=auto_limit,
                unit="requests",
            )
        )
    if (pct_api := _first_float(inner, 13)) is not None:
        windows.append(
            HarnessUsageWindow(
                name="api",
                percent_used=pct_api,
                starts_at=_ms_iso(period_start_ms),
                ends_at=_ms_iso(period_end_ms),
                reset_at=_ms_iso(period_end_ms),
                used=api_used,
                limit=api_limit,
                unit="requests",
            )
        )

    return HarnessUsageStatus(
        harness="cursor",
        source="cursor-api:GetCurrentPeriodUsage",
        fetched_at=utc_now_iso(),
        plan=plan,
        windows=windows,
        messages=_strings(top, 11) + _strings(top, 12),
        raw={
            "period_start_ms": period_start_ms,
            "period_end_ms": period_end_ms,
            "auto_used": auto_used,
            "auto_limit": auto_limit,
            "api_used": api_used,
            "api_limit": api_limit,
        },
    )


def get_usage_status() -> HarnessUsageStatus:
    token = get_access_token()
    try:
        raw = _fetch_raw(token)
    except CursorAPIError as exc:
        if exc.status != 401:
            raise
        # JWT looked usable but the API rejected it — force a refresh and retry once.
        token = get_access_token(force_refresh=True)
        raw = _fetch_raw(token)
    return _status_from_proto(raw)
