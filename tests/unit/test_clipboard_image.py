"""Tests for murder.app.tui.clipboard_image.

COOKBOOK = canonical subprocess-dispatch usage. EDGE CASES = real failure
modes (nonzero exit, empty output, missing binary).
"""

from __future__ import annotations

import asyncio

import pytest

from murder.app.tui.clipboard_image import has_clipboard_image, read_clipboard_image_png


class _FakeProc:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _patch_exec(monkeypatch, returncode: int, stdout: bytes):
    proc = _FakeProc(returncode, stdout)

    async def _fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    return proc


def _patch_capture(monkeypatch, stdout: bytes) -> list[tuple]:
    """Record the argv passed to create_subprocess_exec."""
    calls: list[tuple] = []

    async def _capture(*args, **_kwargs):
        calls.append(args)
        return _FakeProc(0, stdout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
    return calls


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_has_clipboard_image_detects_image_mime(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 0, b"text/plain\nimage/png\n")
    assert asyncio.run(has_clipboard_image()) is True


def test_read_clipboard_image_png_returns_bytes_on_success(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 0, b"\x89PNG\r\n\x1a\n")
    assert asyncio.run(read_clipboard_image_png()) == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("wayland_display", "expected_binary"),
    [(":1", "wl-paste"), (None, "xclip")],
)
def test_selects_backend_binary_by_wayland_display(
    monkeypatch, wayland_display: str | None, expected_binary: str
) -> None:
    if wayland_display is None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    else:
        monkeypatch.setenv("WAYLAND_DISPLAY", wayland_display)
    calls = _patch_capture(monkeypatch, b"image/png\n")
    asyncio.run(has_clipboard_image())
    assert calls[0][0] == expected_binary


# ============================================================
# === EDGE CASES =============================================
# ============================================================


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (b"text/plain\nimage/png\n", True),
        (b"text/plain\ntext/html\n", False),
    ],
)
def test_has_clipboard_image_keys_on_image_mime_presence(
    monkeypatch, stdout: bytes, expected: bool
) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 0, stdout)
    assert asyncio.run(has_clipboard_image()) is expected


def test_has_clipboard_image_false_on_nonzero_exit(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 1, b"image/png\n")
    assert asyncio.run(has_clipboard_image()) is False


def test_has_clipboard_image_false_when_binary_missing(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    async def _raise(*_args, **_kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)
    assert asyncio.run(has_clipboard_image()) is False


def test_read_clipboard_image_png_none_on_nonzero_exit(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 1, b"\x89PNG\r\n\x1a\n")
    assert asyncio.run(read_clipboard_image_png()) is None


def test_read_clipboard_image_png_none_on_empty_output(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _patch_exec(monkeypatch, 0, b"")
    assert asyncio.run(read_clipboard_image_png()) is None


def test_read_clipboard_image_png_none_when_binary_missing(monkeypatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    async def _raise(*_args, **_kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)
    assert asyncio.run(read_clipboard_image_png()) is None
