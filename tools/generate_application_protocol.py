"""Generate the TypeScript application-protocol contract from Pydantic models."""

# ruff: noqa: E501, PLR0911, PLR0912

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from enum import Enum
from pathlib import Path

from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from murder.app.protocol.common import (  # noqa: E402
    APPLICATION_PROTOCOL_VERSION,
    ClientKind,
    ErrorCode,
)
from murder.app.protocol.operations import (  # noqa: E402
    COMMAND_OPERATIONS,
    QUERY_OPERATIONS,
)
from murder.app.protocol.projections import (  # noqa: E402
    PROJECTION_EVENT_MODELS,
    PROJECTION_SNAPSHOT_MODELS,
)
from murder.app.protocol.requests import (  # noqa: E402
    CommandName,
    QueryName,
)
from murder.app.protocol.subscriptions import (  # noqa: E402
    NotificationChannel,
    ProjectionTopic,
)
from murder.app.protocol.wire import ApplicationWireMessage  # noqa: E402

OUTPUT = ROOT / "inktui" / "src" / "generated" / "applicationProtocol.ts"


def _union(enum: type[Enum]) -> str:
    return " | ".join(json.dumps(member.value) for member in enum)


def _ts_type(schema: object, definitions: dict[str, object] | None = None) -> str:
    """Render the JSON-schema subset emitted by our Pydantic DTOs."""
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        ref = schema["$ref"]
        if isinstance(ref, str) and ref.startswith("#/$defs/") and definitions is not None:
            return _ts_type(definitions.get(ref.removeprefix("#/$defs/")), definitions)
        return "unknown"
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            return " | ".join(_ts_type(item, definitions) for item in schema[union_key])
    if "allOf" in schema:
        return " & ".join(_ts_type(item, definitions) for item in schema["allOf"])
    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        item = _ts_type(schema.get("items"), definitions)
        return f"readonly ({item})[]" if " | " in item else f"readonly {item}[]"
    if schema_type == "object" or "properties" in schema or "additionalProperties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        parts: list[str] = []
        if isinstance(properties, dict):
            for name, property_schema in properties.items():
                optional = "" if name in required else "?"
                parts.append(
                    f"readonly {json.dumps(name)}{optional}: {_ts_type(property_schema, definitions)}"
                )
        additional = schema.get("additionalProperties")
        if additional not in (None, False):
            parts.append(f"readonly [key: string]: {_ts_type(additional, definitions)}")
        return "{ " + "; ".join(parts) + " }"
    return "unknown"


def _model_ts(model: object) -> str:
    # Pydantic's TypeAdapter also handles union result models such as the
    # writer-lease reply, so the generator and runtime validator share exactly
    # the same model expression.
    schema = TypeAdapter(model).json_schema()
    definitions = schema.get("$defs")
    return _ts_type(schema, definitions if isinstance(definitions, dict) else None)


def _operation_maps() -> str:
    def render_map(name: str, operations: dict[object, object]) -> str:
        rows = []
        for operation in operations.values():
            # Every operation has a registry DTO.  ``legacy`` records a
            # transitional implementation only; it must never erase the
            # client contract into ``any``.
            params = _model_ts(operation.params_model)
            result = _model_ts(operation.result_model)
            rows.append(
                f"  readonly {json.dumps(operation.name.value)}: {{ readonly params: "
                f"{params}; readonly result: {result} }};"
            )
        return f"export interface {name} {{\n" + "\n".join(rows) + "\n}"

    projection_snapshots = "\n".join(
        f"  readonly {json.dumps(topic.value)}: {_model_ts(model)};"
        for topic, model in PROJECTION_SNAPSHOT_MODELS.items()
    )
    projection_events = "\n".join(
        f"  readonly {json.dumps(topic.value)}: {_model_ts(model)};"
        for topic, model in PROJECTION_EVENT_MODELS.items()
    )
    return "\n\n".join(
        (
            render_map("QueryMethods", QUERY_OPERATIONS),
            render_map("CommandMethods", COMMAND_OPERATIONS),
            "export interface ProjectionSnapshots {\n" + projection_snapshots + "\n}\n"
            "export interface ProjectionEvents {\n" + projection_events + "\n}\n"
            "export type ProjectionSnapshot = Partial<ProjectionSnapshots>;\n"
            "export type ProjectionEvent = ProjectionEvents[keyof ProjectionEvents];",
            "export type QueryMethod = keyof QueryMethods;\n"
            "export type CommandMethod = keyof CommandMethods;\n"
            "export type QueryParams<M extends QueryMethod> = QueryMethods[M]['params'];\n"
            "export type QueryResult<M extends QueryMethod> = QueryMethods[M]['result'];\n"
            "export type CommandParams<M extends CommandMethod> = CommandMethods[M]['params'];\n"
            "export type CommandResult<M extends CommandMethod> = CommandMethods[M]['result'];\n\n"
            "export type QueryResultMap = { readonly [M in QueryMethod]: QueryResult<M> };\n"
            "export type CommandResultMap = { readonly [M in CommandMethod]: CommandResult<M> };",
        )
    )


def render() -> str:
    schema = TypeAdapter(ApplicationWireMessage).json_schema()
    digest = hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    operation_maps = _operation_maps()
    return f"""/* eslint-disable */
// GENERATED by tools/generate_application_protocol.py. DO NOT EDIT.
// Pydantic application-wire schema sha256: {digest}

export const APPLICATION_PROTOCOL_VERSION = {APPLICATION_PROTOCOL_VERSION} as const;

export type ClientKind = {_union(ClientKind)};
export type ErrorCode = {_union(ErrorCode)};
export type QueryName = {_union(QueryName)};
export type CommandName = {_union(CommandName)};
export type ProjectionTopic = {_union(ProjectionTopic)};
export type NotificationChannel = {_union(NotificationChannel)};

export interface ClientHello {{
  readonly op: 'client.hello';
  readonly protocol_version: number;
  readonly client: {{ readonly client_id: string; readonly kind: ClientKind }};
}}

export interface ServerHello {{
  readonly op: 'server.hello';
  readonly protocol_version: number;
  readonly server_id: string;
  readonly queries: readonly QueryName[];
  readonly commands: readonly CommandName[];
  readonly subscriptions: readonly ('projections' | 'notifications' | 'facts')[];
  readonly terminal_streams: boolean;
  readonly fact_cursor: number;
  readonly projection_cursor: number;
}}

{operation_maps}

export type QueryRequest = {{ [M in QueryMethod]: {{ readonly kind: 'query'; readonly name: M; readonly params: QueryParams<M> }} }}[QueryMethod];
export type CommandRequest = {{ [M in CommandMethod]: {{ readonly kind: 'command'; readonly name: M; readonly params: CommandParams<M> }} }}[CommandMethod];
export type ApplicationRequest = QueryRequest | CommandRequest;

export interface RequestMessage {{
  readonly op: 'request';
  readonly request_id: string;
  readonly request: ApplicationRequest;
  readonly timeout_s: number;
}}

export type ReplyMessage = {{
  readonly op: 'reply';
  readonly request_id: string;
}} & ({{ readonly result: QueryResult<QueryMethod> }} | {{ readonly result: CommandResult<CommandMethod> }});

export type SubscriptionSpec =
  | {{
      readonly kind: 'projections';
      readonly topics: readonly ProjectionTopic[];
      readonly cursor?: number | null;
    }}
  | {{
      readonly kind: 'notifications';
      readonly channels: readonly NotificationChannel[];
      readonly cursor?: number | null;
    }}
  | {{
      readonly kind: 'facts';
      readonly fact_kinds: readonly string[];
      readonly cursor?: number | null;
    }};

export interface SubscribeMessage {{
  readonly op: 'subscribe';
  readonly subscription_id: string;
  readonly subscription: SubscriptionSpec;
}}

export interface UnsubscribeMessage {{
  readonly op: 'unsubscribe';
  readonly subscription_id: string;
}}

export interface SubscriptionSnapshot {{
  readonly snapshots: ProjectionSnapshot;
  readonly cursor: number;
  readonly mode: 'cold' | 'resume' | 'snapshot_fallback';
  readonly replay: readonly {{
    readonly cursor: number;
    readonly payload: ProjectionEvent | Record<string, unknown>
  }}[];
}}

export interface SubscriptionReadyMessage {{
  readonly op: 'subscription.ready';
  readonly subscription_id: string;
  readonly snapshot: SubscriptionSnapshot;
}}

export interface SubscriptionEventMessage {{
  readonly op: 'subscription.event';
  readonly subscription_id: string;
  readonly cursor?: number | null;
  readonly payload: ProjectionEvent | Record<string, unknown>;
}}

export interface TerminalAttachMessage {{
  readonly op: 'terminal.attach';
  readonly stream_id: string;
  readonly target: {{
    readonly session_id: string;
  }};
  readonly after_sequence: number;
}}

export interface TerminalDetachMessage {{
  readonly op: 'terminal.detach';
  readonly stream_id: string;
}}

export interface TerminalResyncMessage {{
  readonly op: 'terminal.resync';
  readonly stream_id: string;
  readonly after_sequence: number;
  readonly reason: 'gap' | 'unsupported_mode';
}}

export interface TerminalAttachedMessage {{
  readonly op: 'terminal.attached';
  readonly stream_id: string;
  readonly mode: 'replace';
}}

export interface TerminalFrame {{
  readonly type: 'terminal.frame';
  readonly subscription_id: string;
  readonly session_id: string;
  readonly sequence: number;
  readonly captured_at: string;
  readonly columns: number;
  readonly rows: number;
  readonly encoding: 'utf-8';
  readonly data: string;
  readonly reset: boolean;
}}

export interface TerminalChunk {{
  readonly type: 'terminal.chunk';
  readonly subscription_id: string;
  readonly session_id: string;
  readonly sequence: number;
  readonly encoding: 'utf-8';
  readonly data: string;
}}

export interface TerminalStreamGap {{
  readonly type: 'terminal.gap';
  readonly subscription_id: string;
  readonly session_id: string;
  readonly expected_sequence: number;
  readonly next_sequence: number;
  readonly snapshot_required: boolean;
}}

export interface TerminalFrameMessage {{
  readonly op: 'terminal.frame';
  readonly stream_id: string;
  readonly frame: TerminalFrame;
}}

export interface TerminalChunkMessage {{
  readonly op: 'terminal.chunk';
  readonly stream_id: string;
  readonly chunk: TerminalChunk;
}}

export interface TerminalStreamGapMessage {{
  readonly op: 'terminal.gap';
  readonly stream_id: string;
  readonly gap: TerminalStreamGap;
}}

export interface TerminalResyncedMessage {{
  readonly op: 'terminal.resynced';
  readonly stream_id: string;
  readonly frame: TerminalFrame;
}}

export interface ErrorMessage {{
  readonly op: 'error';
  readonly request_id?: string | null;
  readonly subscription_id?: string | null;
  readonly stream_id?: string | null;
  readonly error: {{
    readonly code: ErrorCode;
    readonly message: string;
    readonly details: Record<string, unknown>;
  }};
}}

export type ClientMessage =
  | ClientHello
  | RequestMessage
  | SubscribeMessage
  | UnsubscribeMessage
  | TerminalAttachMessage
  | TerminalDetachMessage
  | TerminalResyncMessage;

export type ServerMessage =
  | ServerHello
  | ReplyMessage
  | SubscriptionReadyMessage
  | SubscriptionEventMessage
  | TerminalAttachedMessage
  | TerminalFrameMessage
  | TerminalChunkMessage
  | TerminalStreamGapMessage
  | TerminalResyncedMessage
  | ErrorMessage;
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run {Path(__file__).name}")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
