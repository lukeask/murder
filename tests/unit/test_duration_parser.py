"""Unit tests for murder.work.duration.parse_duration."""

from __future__ import annotations

from datetime import timedelta

import pytest

from murder.work.duration import parse_duration


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1d4h3m", timedelta(days=1, hours=4, minutes=3)),
        ("1h1m", timedelta(hours=1, minutes=1)),
        ("34m", timedelta(minutes=34)),
        ("1h", timedelta(hours=1)),
        ("2d", timedelta(days=2)),
        ("  1h ", timedelta(hours=1)),  # surrounding whitespace tolerated
    ],
)
def test_parse_documented_formats(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty
        "   ",  # whitespace only
        "garbage",
        "34",  # bare number, no unit
        "0",  # bare zero, no unit
        "-1h",  # negative
        "5w",  # unknown unit
        "1h30s",  # unknown unit (seconds)
        "3m1h",  # out of order
        "1h1h",  # duplicate unit
        "1.5h",  # non-integer
    ],
)
def test_parse_malformed_raises(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_parse_non_string_raises() -> None:
    with pytest.raises(ValueError):
        parse_duration(90)  # type: ignore[arg-type]
