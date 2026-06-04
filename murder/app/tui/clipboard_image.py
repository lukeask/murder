"""Linux clipboard image detection and read (Wayland + X11)."""

from __future__ import annotations

import asyncio
import os


async def has_clipboard_image() -> bool:
    """Return True if the system clipboard currently holds an image."""
    try:
        if os.environ.get("WAYLAND_DISPLAY"):
            proc = await asyncio.create_subprocess_exec(
                "wl-paste",
                "--list-types",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "xclip",
                "-selection",
                "clipboard",
                "-t",
                "TARGETS",
                "-o",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        stdout, _ = await proc.communicate()
        return proc.returncode == 0 and b"image" in stdout
    except Exception:
        return False


async def read_clipboard_image_png() -> bytes | None:
    """Read a PNG from the system clipboard. Returns None on any failure or empty output."""
    try:
        if os.environ.get("WAYLAND_DISPLAY"):
            proc = await asyncio.create_subprocess_exec(
                "wl-paste",
                "--type",
                "image/png",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "xclip",
                "-selection",
                "clipboard",
                "-t",
                "image/png",
                "-o",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0 or not stdout:
            return None
        return stdout
    except Exception:
        return None
