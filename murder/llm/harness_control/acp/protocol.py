"""ACP JSON-RPC 2.0 framing (``\"jsonrpc\":\"2.0\"`` required on the wire).

Wire shapes match JSON-RPC 2.0 / ACP:

- request: ``{jsonrpc, id, method, params?}``
- response: ``{jsonrpc, id, result}`` or ``{jsonrpc, id, error: {code, message, data?}}``
- notification: ``{jsonrpc, method, params?}`` (no ``id``)
- server→client request: same as request (has ``id``); client replies with that ``id``

Decode tolerates a missing ``jsonrpc`` field (useful in tests) but requires
standard JSON-RPC shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

RequestId: TypeAlias = str | int
Params: TypeAlias = dict[str, Any] | list[Any] | None

JSONRPC_VERSION = "2.0"


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: RequestId
    method: str
    params: Params = None


@dataclass(frozen=True, slots=True)
class RpcNotification:
    method: str
    params: Params = None


@dataclass(frozen=True, slots=True)
class RpcError:
    code: int
    message: str
    data: Any = None


@dataclass(frozen=True, slots=True)
class RpcResponse:
    id: RequestId
    result: Any = None
    error: RpcError | None = None


RpcMessage: TypeAlias = RpcRequest | RpcNotification | RpcResponse


def is_request(message: RpcMessage) -> bool:
    return isinstance(message, RpcRequest)


def is_notification(message: RpcMessage) -> bool:
    return isinstance(message, RpcNotification)


def is_response(message: RpcMessage) -> bool:
    return isinstance(message, RpcResponse)


def is_error_response(message: RpcMessage) -> bool:
    return isinstance(message, RpcResponse) and message.error is not None


def message_kind(message: RpcMessage) -> Literal["request", "notification", "response"]:
    if isinstance(message, RpcRequest):
        return "request"
    if isinstance(message, RpcNotification):
        return "notification"
    return "response"


def encode_message(message: RpcMessage) -> str:
    """Serialize a message to a single JSON object string (no trailing newline).

    Always includes ``\"jsonrpc\": \"2.0\"`` (ACP / JSON-RPC 2.0 requirement).
    """
    payload: dict[str, Any]
    if isinstance(message, RpcRequest):
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": message.id,
            "method": message.method,
        }
        if message.params is not None:
            payload["params"] = message.params
    elif isinstance(message, RpcNotification):
        payload = {"jsonrpc": JSONRPC_VERSION, "method": message.method}
        if message.params is not None:
            payload["params"] = message.params
    elif message.error is not None:
        error: dict[str, Any] = {
            "code": message.error.code,
            "message": message.error.message,
        }
        if message.error.data is not None:
            error["data"] = message.error.data
        payload = {"jsonrpc": JSONRPC_VERSION, "id": message.id, "error": error}
    else:
        payload = {"jsonrpc": JSONRPC_VERSION, "id": message.id, "result": message.result}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def decode_line(line: str | bytes) -> RpcMessage:
    """Decode one JSONL frame into a typed RPC message."""
    if isinstance(line, bytes):
        text = line.decode("utf-8")
    else:
        text = line
    text = text.strip()
    if not text:
        raise ValueError("empty ACP JSONL line")
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"ACP message must be an object, got {type(raw).__name__}")
    return decode_object(raw)


def decode_object(raw: dict[str, Any]) -> RpcMessage:
    """Classify a decoded JSON object into request / response / notification.

    Tolerates a missing ``jsonrpc`` field; if present it must be ``\"2.0\"``.
    """
    if "jsonrpc" in raw:
        version = raw["jsonrpc"]
        if version != JSONRPC_VERSION:
            raise ValueError(f"unsupported jsonrpc version: {version!r}")
        raw = {key: value for key, value in raw.items() if key != "jsonrpc"}

    has_id = "id" in raw
    has_method = "method" in raw
    has_result = "result" in raw
    has_error = "error" in raw

    if has_id and has_method and not has_result and not has_error:
        method = raw["method"]
        if not isinstance(method, str):
            raise ValueError("request method must be a string")
        return RpcRequest(
            id=_require_request_id(raw["id"]),
            method=method,
            params=raw.get("params"),
        )

    if has_id and (has_result or has_error) and not has_method:
        error = None
        if has_error:
            error = _decode_error(raw["error"])
        return RpcResponse(
            id=_require_request_id(raw["id"]),
            result=raw.get("result"),
            error=error,
        )

    if has_method and not has_id:
        method = raw["method"]
        if not isinstance(method, str):
            raise ValueError("notification method must be a string")
        return RpcNotification(method=method, params=raw.get("params"))

    raise ValueError(f"unrecognized ACP message shape: {sorted(raw)}")


def _require_request_id(value: Any) -> RequestId:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"invalid request id type: {type(value).__name__}")
    return value


def _decode_error(value: Any) -> RpcError:
    if not isinstance(value, dict):
        raise ValueError("error must be an object")
    code = value.get("code")
    message = value.get("message")
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError("error.code must be an int")
    if not isinstance(message, str):
        raise ValueError("error.message must be a string")
    return RpcError(code=code, message=message, data=value.get("data"))
