"""Shared retry taxonomy for HTTP-based API clients.

Distinguishes retryable failures (429 / 5xx / transport) from terminal 4xx
client errors that will never succeed on retry, and honors the ``Retry-After``
header when the provider sends one.
"""

from __future__ import annotations

import logging

import httpx

LOGGER = logging.getLogger(__name__)

RATE_LIMITED_STATUS = 429
SERVER_ERROR_STATUS = 500


def is_retryable_status(status_code: int) -> bool:
    """A status is worth retrying only for rate-limit (429) or server (5xx) errors."""
    return status_code == RATE_LIMITED_STATUS or status_code >= SERVER_ERROR_STATUS


def is_retryable_exc(exc: Exception) -> bool:
    """Transport errors are retryable; HTTP status errors only if 429/5xx."""
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retryable_status(exc.response.status_code)
    return isinstance(exc, httpx.RequestError)


def retry_after_seconds(exc: Exception) -> float | None:
    """Parse a ``Retry-After`` header (integer seconds) from a status error, if present."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        # HTTP-date form is not honored here; fall back to backoff.
        return None
