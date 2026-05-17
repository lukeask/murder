"""Cursor usage API client.

Cursor does not expose usage through the interactive agent CLI. This module
reads the local Cursor auth token and calls Cursor's current-period usage API.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from murder.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.harnesses.usage import utc_now_iso

AUTH_URL = "https://api2.cursor.sh/auth/token"
USAGE_URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5


class CursorUsageError(Exception):
    """Base exception for Cursor usage collection failures."""


class CursorNotInstalledError(CursorUsageError):
    """Cursor DB not found."""


class CursorNotAuthenticatedError(CursorUsageError):
    """No usable Cursor auth token was found."""


class CursorAPIError(CursorUsageError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def _db_path() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        )
    return os.path.expanduser("~/.config/Cursor/User/globalStorage/state.vscdb")


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


def _jwt_exp(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data["exp"])
    except Exception:
        return 0


def _refresh_token(refresh_token: str) -> str | None:
    request = urllib.request.Request(
        AUTH_URL,
        data=json.dumps({"refreshToken": refresh_token}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read())
            value = data.get("access_token") or data.get("accessToken")
            return str(value) if value else None
    except Exception:
        return None


def get_access_token() -> str:
    keys = _read_db_keys("cursorAuth/accessToken", "cursorAuth/refreshToken")
    access = keys.get("cursorAuth/accessToken")
    refresh = keys.get("cursorAuth/refreshToken")

    if not access and not refresh:
        raise CursorNotAuthenticatedError("No Cursor auth tokens found")

    if access and _jwt_exp(access) > time.time() + 60:
        return access

    if refresh and (new_token := _refresh_token(refresh)):
        return new_token

    if access:
        return access

    raise CursorNotAuthenticatedError("Cursor access token could not be refreshed")


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


def get_usage_status() -> HarnessUsageStatus:
    token = get_access_token()
    top = _decode_proto(_fetch_raw(token))
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
