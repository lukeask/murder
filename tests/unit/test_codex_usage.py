"""Unit tests for Codex ``account/rateLimits/read`` usage mapping."""

from __future__ import annotations

import asyncio

from murder.llm.harnesses import codex_usage
from murder.llm.harnesses.models import HarnessUsageStatus


def test_rate_limits_to_usage_status_maps_primary_secondary_windows() -> None:
    status = codex_usage.rate_limits_to_usage_status(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 42.5,
                    "windowDurationMins": 300,
                    "resetsAt": 1_717_502_400,  # 2024-06-04T12:00:00Z
                },
                "secondary": {
                    "usedPercent": 11,
                    "windowDurationMins": 10080,
                    "resetsAt": 1_718_107_200,  # 2024-06-11T12:00:00Z
                },
                "planType": "plus",
            },
            "rateLimitsByLimitId": {"x": 1},
        },
        fetched_at="2026-06-04T00:00:00+00:00",
    )

    assert status.harness == "codex"
    assert status.source == "app-server:account/rateLimits/read"
    assert status.plan == "plus"
    assert [window.name for window in status.windows] == ["5h", "weekly"]
    assert status.windows[0].key == "primary"
    assert status.windows[0].percent_used == 42.5
    assert status.windows[0].reset_at is not None
    assert status.windows[1].key == "secondary"
    assert status.windows[1].percent_used == 11.0
    assert status.raw["rateLimitsByLimitId"] == {"x": 1}


def test_rate_limits_accepts_inner_object_without_wrapper() -> None:
    status = codex_usage.rate_limits_to_usage_status(
        {
            "primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": 1_700_000_000},
            "secondary": None,
        },
        fetched_at="2026-06-04T00:00:00+00:00",
    )
    assert [window.name for window in status.windows] == ["5h"]


def test_get_usage_status_issues_rate_limits_read(monkeypatch) -> None:
    requests: list[tuple[str, object]] = []

    class _FakeConnection:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            del args, kwargs
            self.closed = False

        async def start(self) -> None:
            return None

        async def request(self, method: str, params=None):  # noqa: ANN001
            requests.append((method, params))
            return {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 7,
                        "windowDurationMins": 300,
                        "resetsAt": 1_700_000_000,
                    },
                    "secondary": None,
                    "planType": "plus",
                }
            }

        async def aclose(self) -> None:
            self.closed = True

    class _FakeClient:
        def __init__(self, connection: _FakeConnection) -> None:
            self.connection = connection

        async def initialize(self) -> dict[str, object]:
            return {"userAgent": "test"}

    fake = _FakeConnection()
    monkeypatch.setattr(codex_usage, "AppServerConnection", lambda **kwargs: fake)
    monkeypatch.setattr(codex_usage, "AppServerClient", _FakeClient)

    status = asyncio.run(codex_usage.get_usage_status())

    assert isinstance(status, HarnessUsageStatus)
    assert requests == [("account/rateLimits/read", {})]
    assert status.windows[0].percent_used == 7.0
    assert status.plan == "plus"
    assert fake.closed
