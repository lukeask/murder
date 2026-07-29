"""Unit tests for Cursor token refresh and usage HTTP client."""

from __future__ import annotations

import base64
import io
import json
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from murder.llm.harnesses import cursor_usage


def _b64url(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwt(*, exp: int, typ: str = "session") -> str:
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url(
        {
            "sub": "user_test",
            "exp": exp,
            "iss": "https://authentication.cursor.sh",
            "aud": "https://cursor.com",
            "type": typ,
        }
    )
    return f"{header}.{payload}.sig"


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        del args


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", hdrs=None, fp=io.BytesIO(body))


def test_refresh_token_posts_oauth_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        return _FakeResponse(json.dumps({"access_token": "fresh-access"}).encode())

    monkeypatch.setattr(cursor_usage.urllib.request, "urlopen", fake_urlopen)

    token = cursor_usage._refresh_token("refresh-abc")

    assert token == "fresh-access"
    assert captured["url"] == cursor_usage.AUTH_URL
    assert captured["url"].endswith("/oauth/token")
    assert captured["body"] == {
        "grant_type": "refresh_token",
        "client_id": cursor_usage.CURSOR_OAUTH_CLIENT_ID,
        "refresh_token": "refresh-abc",
    }


def test_refresh_token_should_logout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        del request
        return _FakeResponse(
            json.dumps(
                {"access_token": "", "id_token": "", "shouldLogout": True}
            ).encode()
        )

    monkeypatch.setattr(cursor_usage.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(cursor_usage.CursorNotAuthenticatedError, match="rejected session"):
        cursor_usage._refresh_token("dead-refresh")


def test_refresh_token_logs_http_body_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise _http_error(
            request.full_url,
            404,
            b'{"message":"Route POST:/auth/token not found","error":"Not Found","statusCode":404}',
        )

    monkeypatch.setattr(cursor_usage.urllib.request, "urlopen", fake_urlopen)

    assert cursor_usage._refresh_token("x") is None


def test_get_access_token_refreshes_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _jwt(exp=int(time.time()) - 100)
    fresh = _jwt(exp=int(time.time()) + 3600)

    monkeypatch.setattr(
        cursor_usage,
        "_load_token_pairs",
        lambda: [
            cursor_usage._TokenPair(
                access=expired, refresh="refresh-xyz", source="ide-state.vscdb"
            )
        ],
    )
    monkeypatch.setattr(cursor_usage, "_refresh_token", lambda refresh: fresh if refresh else None)

    assert cursor_usage.get_access_token() == fresh


def test_get_access_token_prefers_fresher_agent_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _jwt(exp=int(time.time()) - 100)
    live = _jwt(exp=int(time.time()) + 3600)

    monkeypatch.setattr(
        cursor_usage,
        "_load_token_pairs",
        lambda: [
            cursor_usage._TokenPair(access=live, refresh=live, source="agent-auth.json"),
            cursor_usage._TokenPair(access=stale, refresh=stale, source="ide-state.vscdb"),
        ],
    )

    def boom(refresh: str) -> str | None:
        del refresh
        raise AssertionError("should not refresh when a valid access token exists")

    monkeypatch.setattr(cursor_usage, "_refresh_token", boom)
    assert cursor_usage.get_access_token() == live


def test_get_access_token_skips_rejected_ide_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _jwt(exp=int(time.time()) - 100)
    live_refresh = "agent-refresh"
    fresh = _jwt(exp=int(time.time()) + 3600)

    monkeypatch.setattr(
        cursor_usage,
        "_load_token_pairs",
        lambda: [
            cursor_usage._TokenPair(
                access=stale, refresh=live_refresh, source="agent-auth.json"
            ),
            cursor_usage._TokenPair(access=stale, refresh="ide-refresh", source="ide-state.vscdb"),
        ],
    )

    def refresh(tok: str) -> str | None:
        if tok == "ide-refresh":
            raise cursor_usage.CursorNotAuthenticatedError("rejected")
        if tok == live_refresh:
            return fresh
        return None

    monkeypatch.setattr(cursor_usage, "_refresh_token", refresh)
    # Both access tokens expired; agent refresh succeeds after ide rejects.
    # Ordering is by max(access, refresh) exp — both stale have same access exp,
    # so either order may try ide first; both paths must still land on fresh.
    assert cursor_usage.get_access_token(force_refresh=True) == fresh


def test_get_usage_status_retries_once_after_401(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = _jwt(exp=int(time.time()) + 3600)
    refreshed = _jwt(exp=int(time.time()) + 7200)
    tokens = iter([valid, refreshed])
    fetches: list[str] = []

    # Minimal protobuf: empty message is enough for the decoder; windows stay empty.
    usage_body = b""

    monkeypatch.setattr(cursor_usage, "get_access_token", lambda **kwargs: next(tokens))

    def fake_fetch(token: str) -> bytes:
        fetches.append(token)
        if token == valid:
            raise cursor_usage.CursorAPIError(401, "unauthenticated")
        return usage_body

    monkeypatch.setattr(cursor_usage, "_fetch_raw", fake_fetch)

    status = cursor_usage.get_usage_status()

    assert status.harness == "cursor"
    assert fetches == [valid, refreshed]
    assert status.windows == []


def test_get_usage_status_does_not_retry_non_401(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = _jwt(exp=int(time.time()) + 3600)
    monkeypatch.setattr(cursor_usage, "get_access_token", lambda **kwargs: valid)

    def fake_fetch(token: str) -> bytes:
        del token
        raise cursor_usage.CursorAPIError(500, "boom")

    monkeypatch.setattr(cursor_usage, "_fetch_raw", fake_fetch)

    with pytest.raises(cursor_usage.CursorAPIError) as excinfo:
        cursor_usage.get_usage_status()
    assert excinfo.value.status == 500


def test_get_usage_status_second_401_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = _jwt(exp=int(time.time()) + 3600)
    refreshed = _jwt(exp=int(time.time()) + 7200)
    tokens = iter([valid, refreshed])

    monkeypatch.setattr(cursor_usage, "get_access_token", lambda **kwargs: next(tokens))
    monkeypatch.setattr(
        cursor_usage,
        "_fetch_raw",
        lambda token: (_ for _ in ()).throw(cursor_usage.CursorAPIError(401, "still bad")),
    )

    with pytest.raises(cursor_usage.CursorAPIError) as excinfo:
        cursor_usage.get_usage_status()
    assert excinfo.value.status == 401
