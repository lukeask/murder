#!/usr/bin/env python3
"""Pretend to be a fresh TUI/WebUI client and time initial data hydration.

Connects to the murder supervisor bus for a project repo (a directory containing
``.murder``), performs the same hello handshake, subscriptions, and eager
``primeSlices`` RPC pulls that a real Ink/Web client issues on (re)connect, and
logs every inbound frame with monotonic timestamps until hydration finishes.

Usage::

    python tools/ui_hydration_probe.py /path/to/repo
    python tools/ui_hydration_probe.py . --kind web --tail 2
    python tools/ui_hydration_probe.py . --json > hydration.jsonl

Requires ``murder serviced`` (or ``murder up``) already running for the repo.
The socket path is resolved the same way the CLI does (``socket_path_for_repo``
under ``$XDG_RUNTIME_DIR``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from murder.bus.protocol import (
    PROTOCOL_VERSION,
    WIRE_MESSAGE_ADAPTER,
    AckMessage,
    ClientKind,
    ErrMessage,
    EventFilter,
    HelloBody,
    HelloMessage,
    PubMessage,
    RpcArgs,
    RpcMessage,
    SubArgs,
    SubMessage,
    WakeMessage,
)
from murder.bus.transport_socket import UdsTransport
from murder.state.storage.paths import MURDER_DIR_NAME
from murder.state.storage.service_registry import socket_path_for_repo

# Mirrors inktui `primeSlices` + store.ts subscription filters.
SUBSCRIPTIONS: tuple[tuple[str, EventFilter], ...] = (
    ("state.snapshot", EventFilter(type="state.snapshot")),
    ("conversation.block", EventFilter(type="conversation.block")),
    ("conversation.state", EventFilter(type="conversation.state")),
    ("error", EventFilter(type="error")),
)

# Mirrors inktui `primeSlices` eager pulls. `state.schedule_snapshot` is issued
# twice in the real client (usage + tickets slices).
PRIME_RPCS: tuple[tuple[str, str], ...] = (
    ("roster", "state.crow_snapshot"),
    ("usage", "state.schedule_snapshot"),
    ("tickets", "state.schedule_snapshot"),
    ("conversations", "state.conversations_snapshot"),
    ("favorites", "tui.load_favorites"),
    ("templates", "tui.load_templates"),
    ("workflows", "tui.load_workflows"),
    ("themes", "tui.load_themes"),
    ("settings", "settings.get"),
)

DEFAULT_RPC_TIMEOUT_S = 30.0


def _resolve_repo(path: Path) -> Path:
    candidate = path.resolve()
    if (candidate / MURDER_DIR_NAME).is_dir():
        return candidate
    raise SystemExit(f"not a murder project (missing {MURDER_DIR_NAME}/): {candidate}")


def _json_size(value: Any) -> int:
    return len(json.dumps(value, default=str))


def _unwrap_rpc_result(target: str, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if target.startswith("state.") and result.get("ok") is True and "value" in result:
        value = result["value"]
        return value if isinstance(value, dict) else {"value": value}
    return result


def _summarize_result(target: str, result: dict[str, Any] | None) -> dict[str, Any]:
    payload = _unwrap_rpc_result(target, result)
    if not payload:
        return {"bytes": 0, "keys": []}
    summary: dict[str, Any] = {"bytes": _json_size(payload), "keys": sorted(payload)}
    if target == "state.crow_snapshot":
        summary["sessions"] = len(payload.get("sessions") or [])
    elif target == "state.schedule_snapshot":
        summary["active_tickets"] = len(payload.get("active") or [])
        summary["usage_gauges"] = len(payload.get("usage_gauges") or [])
    elif target == "state.conversations_snapshot":
        agents = payload.get("agents") or []
        summary["agents"] = len(agents)
        summary["transcript_blocks"] = sum(len(a.get("blocks") or []) for a in agents)
    elif target.startswith("tui.load_"):
        for key in ("favorites", "templates", "workflows", "themes"):
            if key in payload:
                items = payload[key]
                summary[key] = len(items) if isinstance(items, list) else 1
    elif target == "settings.get":
        settings = payload.get("settings")
        if isinstance(settings, dict):
            summary["settings_keys"] = sorted(settings)
    return summary


def _summarize_pub(event: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "type": event.get("type"),
        "bytes": _json_size(event),
    }
    if event.get("type") == "state.snapshot":
        summary["entity"] = event.get("entity")
        summary["entity_id"] = event.get("entity_id")
    elif event.get("type") in {"conversation.block", "conversation.state"}:
        summary["agent_id"] = event.get("agent_id")
    elif event.get("type") == "error":
        summary["message"] = event.get("message")
    return summary


@dataclass
class HydrationProbe:
    repo: Path
    client_kind: ClientKind
    rpc_timeout_s: float
    tail_s: float
    json_mode: bool
    t0: float = field(default_factory=time.monotonic)
    events: list[dict[str, Any]] = field(default_factory=list)
    rpc_pending: dict[str, tuple[str, str]] = field(default_factory=dict)
    rpc_done: set[str] = field(default_factory=set)
    sub_pending: set[str] = field(default_factory=set)
    sub_replay_done: set[str] = field(default_factory=set)
    handshake_done: bool = False
    hydration_done: bool = False
    hydration_at: float | None = None

    def _elapsed_ms(self) -> float:
        return (time.monotonic() - self.t0) * 1000.0

    def _log(self, kind: str, detail: dict[str, Any]) -> None:
        row = {"t_ms": round(self._elapsed_ms(), 1), "kind": kind, **detail}
        self.events.append(row)
        if self.json_mode:
            print(json.dumps(row, default=str), flush=True)
            return
        extra = " ".join(f"{k}={v}" for k, v in detail.items())
        print(f"[{row['t_ms']:8.1f}ms] {kind:<14} {extra}", flush=True)

    async def _send_json(self, session: _JsonSession, message: object) -> None:
        payload = json.dumps(message.model_dump(mode="json"), default=str) + "\n"
        await session.transport.send(payload.encode())

    async def _subscribe_all(self, session: _JsonSession) -> None:
        for label, filt in SUBSCRIPTIONS:
            correlation_id = f"sub-{uuid4().hex}"
            self.sub_pending.add(correlation_id)
            await self._send_json(
                session,
                SubMessage(
                    correlation_id=correlation_id,
                    args=SubArgs(filter=filt),
                ),
            )
            self._log("sub_sent", {"label": label, "filter_type": filt.type})

    async def _prime_all(self, session: _JsonSession) -> None:
        for label, target in PRIME_RPCS:
            correlation_id = f"rpc-{uuid4().hex}"
            self.rpc_pending[correlation_id] = (label, target)
            await self._send_json(
                session,
                RpcMessage(
                    correlation_id=correlation_id,
                    args=RpcArgs(target=target, body={}, timeout_s=self.rpc_timeout_s),
                ),
            )
            self._log("rpc_sent", {"label": label, "target": target})

    def _maybe_note_hydration_done(self) -> None:
        if self.hydration_done or not self.handshake_done:
            return
        if self.sub_pending and not self.sub_pending.issubset(self.sub_replay_done):
            return
        if self.rpc_pending:
            return
        self.hydration_done = True
        self.hydration_at = time.monotonic()
        self._log(
            "hydration_done",
            {
                "rpc_count": len(self.rpc_done),
                "pub_count": sum(1 for e in self.events if e["kind"] == "pub"),
            },
        )

    def _handle_inbound(self, msg: object) -> None:
        if isinstance(msg, WakeMessage):
            self._log(
                "wake",
                {
                    "reason": msg.body.reason,
                    "fresh_state_hints": [e.value for e in msg.body.fresh_state_hints],
                },
            )
            return
        if isinstance(msg, ErrMessage):
            self._log("err", {"code": msg.body.code, "message": msg.body.message})
            return
        if isinstance(msg, AckMessage):
            body = msg.body
            if msg.correlation_id in self.rpc_pending:
                label, target = self.rpc_pending.pop(msg.correlation_id)
                self.rpc_done.add(msg.correlation_id)
                self._log(
                    "rpc_ack",
                    {
                        "label": label,
                        "target": target,
                        "summary": _summarize_result(target, body.result),
                    },
                )
                self._maybe_note_hydration_done()
                return
            if msg.correlation_id in self.sub_pending:
                if body.kind == "subscribed":
                    self._log("sub_ack", {"correlation_id": msg.correlation_id, "kind": "subscribed"})
                if body.kind == "replay_done":
                    self.sub_replay_done.add(msg.correlation_id)
                    self._log(
                        "sub_ack",
                        {
                            "correlation_id": msg.correlation_id,
                            "kind": "replay_done",
                            "watermark": body.watermark,
                        },
                    )
                    self._maybe_note_hydration_done()
                return
            self._log("ack", {"correlation_id": msg.correlation_id, "kind": body.kind})
            return
        if isinstance(msg, PubMessage):
            event = msg.event.model_dump(mode="json")
            self._log("pub", {"summary": _summarize_pub(event)})
            return
        self._log("frame", {"op": getattr(msg, "op", type(msg).__name__)})

    async def _reader_loop(self, session: _JsonSession, inbound: asyncio.Queue[object | None]) -> None:
        try:
            while True:
                msg = await session.recv(timeout_s=self.rpc_timeout_s)
                await inbound.put(msg)
        except asyncio.TimeoutError:
            await inbound.put(None)
        except RuntimeError as exc:
            if "bus socket closed" not in str(exc):
                raise
            await inbound.put(None)

    async def _wait_for_hello_ack(
        self,
        inbound: asyncio.Queue[object | None],
        hello_correlation_id: str,
    ) -> None:
        while True:
            msg = await asyncio.wait_for(inbound.get(), timeout=self.rpc_timeout_s)
            if msg is None:
                raise RuntimeError("bus socket closed during handshake")
            if isinstance(msg, ErrMessage):
                raise RuntimeError(msg.body.message)
            if isinstance(msg, AckMessage) and msg.correlation_id == hello_correlation_id:
                self.handshake_done = True
                self._log("hello_ack", {})
                return
            self._handle_inbound(msg)

    async def run(self, socket_path: Path) -> int:
        transport = UdsTransport(subscription_idle_timeout=max(self.tail_s + 30.0, 60.0))
        try:
            await transport.connect(socket_path)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            raise SystemExit(
                f"cannot connect to bus socket {socket_path}: {exc}\n"
                "Is `murder serviced` / `murder up` running for this repo?"
            ) from exc

        session = _JsonSession(transport)
        inbound: asyncio.Queue[object | None] = asyncio.Queue()
        reader = asyncio.create_task(self._reader_loop(session, inbound))
        try:
            hello_correlation_id = f"hello-{uuid4().hex}"
            hello = HelloMessage(
                correlation_id=hello_correlation_id,
                body=HelloBody(
                    protocol_version=PROTOCOL_VERSION,
                    client_kind=self.client_kind,
                    client_id=f"{self.client_kind.value}-probe-{uuid4().hex[:8]}",
                ),
            )
            self._log("hello_sent", {"client_kind": self.client_kind.value})
            await self._send_json(session, hello)
            await self._wait_for_hello_ack(inbound, hello_correlation_id)

            await self._subscribe_all(session)
            await self._prime_all(session)

            tail_deadline = None
            while True:
                if self.hydration_done and tail_deadline is None:
                    tail_deadline = time.monotonic() + self.tail_s
                if tail_deadline is not None and time.monotonic() >= tail_deadline:
                    break
                try:
                    msg = await asyncio.wait_for(inbound.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    if tail_deadline is not None and time.monotonic() >= tail_deadline:
                        break
                    continue
                if msg is None:
                    self._log("socket_closed", {})
                    break
                self._handle_inbound(msg)

            if not self.hydration_done:
                self._maybe_note_hydration_done()
        finally:
            await session.close()
            if not reader.done():
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader

        self._print_summary()
        return 0

    def _print_summary(self) -> None:
        if self.json_mode:
            return
        hydration_ms = next(
            (e["t_ms"] for e in self.events if e["kind"] == "hydration_done"),
            None,
        )
        hello_ms = next((e["t_ms"] for e in self.events if e["kind"] == "hello_ack"), None)
        rpc_acks = [e for e in self.events if e["kind"] == "rpc_ack"]
        pubs = [e for e in self.events if e["kind"] == "pub"]

        print()
        print("── summary ──")
        print(f"repo:            {self.repo}")
        print(f"client_kind:     {self.client_kind.value}")
        if hello_ms is not None:
            print(f"hello_ack:       {hello_ms:.1f} ms")
        if hydration_ms is not None:
            print(f"hydration_done:  {hydration_ms:.1f} ms")
        else:
            print("hydration_done:  (incomplete — missing rpc or replay_done)")
        print(f"rpc replies:     {len(rpc_acks)}/{len(PRIME_RPCS)}")
        print(f"pub events:      {len(pubs)}")
        if rpc_acks:
            print("rpc timings:")
            for row in rpc_acks:
                summary = row.get("summary", {})
                size = summary.get("bytes", "?")
                print(f"  {row['t_ms']:8.1f} ms  {row['label']:<14} {row['target']}  ({size} B)")


class _JsonSession:
    """JSON-lines framing over a connected ``UdsTransport`` (mirrors bus client)."""

    def __init__(self, transport: UdsTransport) -> None:
        self.transport = transport
        self._buf = bytearray()

    async def close(self) -> None:
        await self.transport.close()

    async def recv(self, *, timeout_s: float) -> object:
        line = await self._readline(timeout_s=timeout_s)
        return WIRE_MESSAGE_ADAPTER.validate_json(line.decode("utf-8"))

    async def _readline(self, *, timeout_s: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[: nl + 1])
                del self._buf[: nl + 1]
                return line
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            chunk = await asyncio.wait_for(self.transport.recv(), timeout=remaining)
            if not chunk:
                raise RuntimeError("bus socket closed")
            self._buf.extend(chunk)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo",
        type=Path,
        help="project repo root (directory containing .murder/)",
    )
    parser.add_argument(
        "--kind",
        choices=("tui", "web"),
        default="tui",
        help="client kind to advertise in the hello handshake (default: tui)",
    )
    parser.add_argument(
        "--tail",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="keep reading pub frames after hydration completes (default: 1)",
    )
    parser.add_argument(
        "--rpc-timeout",
        type=float,
        default=DEFAULT_RPC_TIMEOUT_S,
        metavar="SECONDS",
        help=f"per-RPC server timeout (default: {DEFAULT_RPC_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit one JSON object per line instead of human-readable log",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    repo = _resolve_repo(args.repo)
    socket_path = socket_path_for_repo(repo)
    kind = ClientKind.TUI if args.kind == "tui" else ClientKind.WEB

    if not args.json:
        print(f"ui hydration probe → {repo.name} ({kind.value})")
        print(f"socket: {socket_path}")
        print()

    probe = HydrationProbe(
        repo=repo,
        client_kind=kind,
        rpc_timeout_s=args.rpc_timeout,
        tail_s=args.tail,
        json_mode=args.json,
    )
    return asyncio.run(probe.run(socket_path))


if __name__ == "__main__":
    raise SystemExit(main())
