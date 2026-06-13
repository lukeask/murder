"""aiohttp server: static SPA serving + a ``/bus`` WebSocket relay to the unix bus.

The relay is a dumb 1:1 byte pump (the WS BRIDGE CONTRACT):

* ``GET /bus`` upgrades to a WebSocket; for each accepted WS connection we open
  ONE fresh connection to the murder unix socket.
* WS text inbound  → write ``text + "\n"`` to the unix socket.
* unix socket bytes → buffer, split on ``\n``; each COMPLETE line (newline
  stripped) is sent as ONE WS text message. Partial lines buffer across reads.
* If either side closes/errors we close the other and clean up.

No protocol interpretation happens here. PROTOCOL_VERSION handshakes, RPC,
subscriptions, presence, etc. all live in the browser.

``aiohttp`` is an OPTIONAL dependency (``pip install 'murder[web]'``). It is
imported lazily so the rest of the CLI works without it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web as aioweb

LOGGER = logging.getLogger(__name__)

# How big a single unix-socket read may be before we flush framed lines.
_READ_CHUNK = 65536


class AiohttpMissingError(RuntimeError):
    """aiohttp (the ``web`` extra) is not installed."""

    def __init__(self) -> None:
        super().__init__(
            "the murder web bridge needs aiohttp, which isn't installed.\n"
            "Install it with:  pip install 'murder[web]'"
        )


def _require_aiohttp() -> Any:
    """Import and return the ``aiohttp.web`` module, or raise AiohttpMissingError."""
    try:
        from aiohttp import web as aioweb
    except ImportError as exc:  # pragma: no cover - exercised via CLI error path
        raise AiohttpMissingError() from exc
    return aioweb


# ---------------------------------------------------------------------------
# Static asset resolution
# ---------------------------------------------------------------------------


def _packaged_assets_dir() -> Path:
    """Location of the packaged (wheel) frontend bundle: ``murder/_webui``.

    Factored out so tests can monkeypatch the lookup at a guaranteed
    nonexistent path, independent of whether a local wheel build left a
    real ``murder/_webui`` artifact on disk.
    """
    return Path(__file__).resolve().parent.parent / "_webui"


def resolve_assets_dir(repo_root: Path) -> Path:
    """Locate the compiled web frontend's static assets directory.

    Resolution order:
      1. Packaged wheel: ``murder/_webui`` (force-included, gitignored).
      2. Source checkout fallback: ``<repo>/webui/dist``.

    Raises FileNotFoundError (with a clear message) if neither exists.
    """
    packaged = _packaged_assets_dir()
    if packaged.is_dir():
        return packaged
    source = repo_root / "webui" / "dist"
    if source.is_dir():
        return source
    raise FileNotFoundError(
        "No web frontend found: neither the packaged bundle "
        f"({packaged}) nor a source build ({source}) is present. "
        "Build the frontend (in webui/) or reinstall murder."
    )


# ---------------------------------------------------------------------------
# The WS <-> unix-socket relay
# ---------------------------------------------------------------------------


async def _pump_socket_to_ws(
    reader: asyncio.StreamReader,
    ws: aioweb.WebSocketResponse,
) -> None:
    """Read newline-framed JSON from the unix socket; forward each complete
    line as one WS text message. Buffers partial lines across reads."""
    buffer = b""
    while True:
        chunk = await reader.read(_READ_CHUNK)
        if not chunk:
            break  # unix socket EOF
        buffer += chunk
        while True:
            nl = buffer.find(b"\n")
            if nl == -1:
                break
            line = buffer[:nl]
            buffer = buffer[nl + 1 :]
            if ws.closed:
                return
            await ws.send_str(line.decode("utf-8"))


async def _pump_ws_to_socket(
    ws: aioweb.WebSocketResponse,
    writer: asyncio.StreamWriter,
) -> None:
    """Read WS text messages; write ``text + "\n"`` to the unix socket."""
    aioweb = _require_aiohttp()
    from aiohttp import WSMsgType

    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            writer.write((msg.data + "\n").encode("utf-8"))
            await writer.drain()
        elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
            break


async def relay(
    ws: aioweb.WebSocketResponse,
    socket_path: Path,
) -> None:
    """Open one unix-socket connection and bidirectionally relay it to ``ws``.

    Closes both sides and cleans up when either side closes or errors.
    """
    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
    except (FileNotFoundError, ConnectionError, OSError) as exc:
        LOGGER.warning("web bridge: cannot reach bus socket %s: %s", socket_path, exc)
        if not ws.closed:
            await ws.close(code=1011, message=b"bus unreachable")
        return

    s2w = asyncio.create_task(_pump_socket_to_ws(reader, ws), name="web-bridge-s2w")
    w2s = asyncio.create_task(_pump_ws_to_socket(ws, writer), name="web-bridge-w2s")
    try:
        # The first task to finish (either side closed/errored) ends the relay.
        _done, pending = await asyncio.wait(
            {s2w, w2s}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Surface relay-loop exceptions to the log (not the WS peer).
        for task in _done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                LOGGER.debug("web bridge relay task errored", exc_info=exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        if not ws.closed:
            await ws.close()


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------


def _make_bus_handler(socket_path: Path):  # type: ignore[no-untyped-def]
    aioweb = _require_aiohttp()

    async def _bus(request: aioweb.Request) -> aioweb.WebSocketResponse:
        ws = aioweb.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        await relay(ws, socket_path)
        return ws

    return _bus


def _make_health_handler(socket_path: Path):  # type: ignore[no-untyped-def]
    aioweb = _require_aiohttp()

    async def _health(_request: aioweb.Request) -> aioweb.Response:
        reachable = False
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(socket_path)), timeout=0.5
            )
            reachable = True
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            del reader
        except Exception:  # noqa: BLE001
            reachable = False
        return aioweb.json_response({"ok": True, "bus_reachable": reachable})

    return _health


def _make_static_handler(assets_dir: Path):  # type: ignore[no-untyped-def]
    aioweb = _require_aiohttp()
    index = assets_dir / "index.html"

    async def _static(request: aioweb.Request) -> aioweb.StreamResponse:
        rel = request.match_info.get("path", "")
        # Resolve under assets_dir, guarding against path traversal.
        candidate = (assets_dir / rel).resolve()
        try:
            candidate.relative_to(assets_dir.resolve())
        except ValueError:
            raise aioweb.HTTPForbidden() from None
        if rel and candidate.is_file():
            return aioweb.FileResponse(candidate)
        # SPA fallback: unknown non-asset paths return index.html.
        if index.is_file():
            return aioweb.FileResponse(index)
        raise aioweb.HTTPNotFound()

    return _static


def create_app(socket_path: Path, assets_dir: Path) -> aioweb.Application:
    """Build the aiohttp application: ``/bus`` WS relay, ``/api/health``, and
    static SPA serving for everything else."""
    aioweb = _require_aiohttp()
    app = aioweb.Application()
    app.router.add_get("/bus", _make_bus_handler(socket_path))
    app.router.add_get("/api/health", _make_health_handler(socket_path))
    app.router.add_get("/{path:.*}", _make_static_handler(assets_dir))
    return app


async def run_server(
    *,
    host: str,
    port: int,
    socket_path: Path,
    assets_dir: Path,
) -> None:
    """Run the bridge server forever (until cancelled / SIGTERM).

    Intended to be driven by ``asyncio.run`` from the daemon worker.
    """
    aioweb = _require_aiohttp()
    app = create_app(socket_path, assets_dir)
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, host, port)
    await site.start()
    LOGGER.info("murder web bridge serving on http://%s:%d (bus=%s)", host, port, socket_path)
    try:
        # Sleep forever; the worker installs a SIGTERM handler that cancels us.
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
