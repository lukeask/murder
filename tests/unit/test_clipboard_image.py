"""Tests for murder.app.tui.clipboard_image."""

from __future__ import annotations

import asyncio

import murder.app.tui.clipboard_image as clip_mod
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


class TestHasClipboardImage:
    def test_returns_true_when_output_contains_image(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 0, b"text/plain\nimage/png\n")
        assert asyncio.run(has_clipboard_image()) is True

    def test_returns_false_when_output_lacks_image(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 0, b"text/plain\ntext/html\n")
        assert asyncio.run(has_clipboard_image()) is False

    def test_returns_false_on_nonzero_exit(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 1, b"image/png\n")
        assert asyncio.run(has_clipboard_image()) is False

    def test_returns_false_on_exception(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        async def _raise(*_args, **_kwargs):
            raise OSError("no such binary")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)
        assert asyncio.run(has_clipboard_image()) is False

    def test_wayland_path_when_display_set(self, monkeypatch) -> None:
        monkeypatch.setenv("WAYLAND_DISPLAY", ":1")
        calls: list[tuple] = []

        async def _capture(*args, **_kwargs):
            calls.append(args)
            return _FakeProc(0, b"image/png\n")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
        asyncio.run(has_clipboard_image())
        assert calls[0][0] == "wl-paste"

    def test_xclip_path_when_display_not_set(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        calls: list[tuple] = []

        async def _capture(*args, **_kwargs):
            calls.append(args)
            return _FakeProc(0, b"image/png\n")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
        asyncio.run(has_clipboard_image())
        assert calls[0][0] == "xclip"


class TestReadClipboardImagePng:
    def test_returns_bytes_on_success(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 0, b"\x89PNG\r\n\x1a\n")
        result = asyncio.run(read_clipboard_image_png())
        assert result == b"\x89PNG\r\n\x1a\n"

    def test_returns_none_on_nonzero_exit(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 1, b"\x89PNG\r\n\x1a\n")
        assert asyncio.run(read_clipboard_image_png()) is None

    def test_returns_none_on_empty_output(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        _patch_exec(monkeypatch, 0, b"")
        assert asyncio.run(read_clipboard_image_png()) is None

    def test_returns_none_on_exception(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        async def _raise(*_args, **_kwargs):
            raise OSError("no such binary")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)
        assert asyncio.run(read_clipboard_image_png()) is None

    def test_wayland_path_when_display_set(self, monkeypatch) -> None:
        monkeypatch.setenv("WAYLAND_DISPLAY", ":1")
        calls: list[tuple] = []

        async def _capture(*args, **_kwargs):
            calls.append(args)
            return _FakeProc(0, b"\x89PNG")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
        asyncio.run(read_clipboard_image_png())
        assert calls[0][0] == "wl-paste"

    def test_xclip_path_when_display_not_set(self, monkeypatch) -> None:
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        calls: list[tuple] = []

        async def _capture(*args, **_kwargs):
            calls.append(args)
            return _FakeProc(0, b"\x89PNG")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)
        asyncio.run(read_clipboard_image_png())
        assert calls[0][0] == "xclip"
