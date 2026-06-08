/**
 * Frozen wire contract between the murder service and its clients.
 *
 * This is the TypeScript port of `murder/bus/protocol.py` — the single source of truth for the
 * JSON-RPC-over-Unix-socket bus. The service *implements* this surface; the Ink store *consumes*
 * it. The two halves build against this file alone, so they can evolve in parallel without
 * reading each other's internals.
 *
 * Faithfulness over invention: every shape, discriminator, and constant here mirrors the Python
 * source. When the Python contract changes, change this file in lockstep. `PROTOCOL_VERSION` MUST
 * equal the Python `PROTOCOL_VERSION`; the client refuses a mismatched server on connect (C2).
 *
 * Out of scope (rule 4): no sockets, no Ink, no framing loops, no transport. Types and constants
 * only — the same boundary the Python module draws ("if you find yourself importing asyncio here,
 * you're in the wrong file"). Framing/handshake logic lives in the `UdsBusClient` (C2).
 */

export const PROTOCOL_VERSION = 1;

// === Closed enums ============================================================
// Modeled as union types of string literals rather than TS `enum`s: the wire carries the bare
// string, `verbatimModuleSyntax`/`isolatedModules` discourage runtime `enum`, and a literal union
// is the idiomatic discriminant for the switch-heavy dispatch the store does. Adding a value here
// must bump PROTOCOL_VERSION, exactly as in the Python source.

export type Role =
  | 'collaborator'
  | 'notetaker'
  | 'planner'
  | 'planning_handler'
  | 'crow_handler'
  | 'crow';

export type AgentStatus =
  | 'idle'
  | 'running'
  | 'blocked'
  | 'escalating'
  | 'done'
  | 'failed'
  | 'dead';

export type CommandStatus = 'pending' | 'in_flight' | 'done' | 'failed' | 'cancelled';

/** Entity kind named by a key-only {@link StateSnapshotEvent}. Closed — adding a value bumps
 * PROTOCOL_VERSION. These are the slice names the store invalidates against. */
export type Entity = 'ticket' | 'agent' | 'plan' | 'note' | 'escalation' | 'queue_row';

export type PresenceState = 'attended' | 'headless';

export type ClientKind = 'tui' | 'web' | 'cli_ephemeral' | 'worker';

// === Inner events (server -> view; discriminated by `type`) ===================
// Persisted to the service's events table. The store reacts to `state.snapshot` (slice
// invalidation) and `conversation.block` (transcript append); the rest are carried for
// completeness so the discriminated union is exhaustive and the compiler can prove a switch covers
// every kind (tsconfig `noFallthroughCasesInSwitch` / `noImplicitReturns`).

/** Fields common to every inner event. Mirrors Python `_BaseEvent`. `id`/`ts` are server-stamped
 * (UUID / ISO-8601 string on the wire); the optional fields are absent when the server omits
 * them. */
interface BaseEvent {
  id: string;
  ts: string;
  run_id: string;
  agent_id: string;
  role?: Role | null;
  ticket_id?: string | null;
}

export interface HeartbeatEvent extends BaseEvent {
  type: 'heartbeat';
  state: 'progressing' | 'stuck' | 'thinking';
  summary?: string | null;
  since_change_s: number;
}

export interface SummaryEvent extends BaseEvent {
  type: 'summary';
  text: string;
  checklist_done: number;
  checklist_total: number;
  last_message_excerpt: string;
}

export interface QuestionEvent extends BaseEvent {
  type: 'question';
  question: string;
  crow_session: string;
  recent_pane: string;
}

export interface EscalationEvent extends BaseEvent {
  type: 'escalation';
  to: 'user' | 'collaborator';
  reason: string;
  severity: 1 | 2 | 3;
  crow_session?: string | null;
  source_event_id?: string | null;
}

export interface StatusChangeEvent extends BaseEvent {
  type: 'status_change';
  entity: 'agent' | 'ticket';
  entity_id: string;
  from_status: string;
  to_status: string;
  reason?: string | null;
}

export interface ErrorEvent extends BaseEvent {
  type: 'error';
  message: string;
  recoverable: boolean;
  traceback?: string | null;
}

export interface CommandEvent extends BaseEvent {
  type: 'command';
  target_worker: string;
  kind: string;
  payload: Record<string, unknown>;
  correlation_id: string;
  idempotency_key: string;
  status: CommandStatus;
  claimed_by?: string | null;
  lease_expires_at?: number | null;
  attempt_count: number;
  retryable: boolean;
  result?: Record<string, unknown> | null;
}

/** Key-only notification that an entity changed. The body lives in the service; the store re-pulls
 * the named slice on receipt (the perf story that replaced poll-everything-every-tick). This is
 * the event the store subscribes to for slice invalidation. */
export interface StateSnapshotEvent extends BaseEvent {
  type: 'state.snapshot';
  entity: Entity;
  key: string;
  entity_version: number;
}

export interface PresenceEvent extends BaseEvent {
  type: 'presence';
  state: PresenceState;
  user_count: number;
  /** Per-{@link ClientKind} connection counts, keyed by the kind string. */
  kinds: Record<string, number>;
  /** Monotonic per supervisor lifetime; subscribers ignore non-increasing versions. */
  version: number;
}

export interface SchedulerModeEvent extends BaseEvent {
  type: 'scheduler.mode';
  from_mode: string;
  to_mode: string;
  changed_by: 'user' | 'api';
}

export interface SchedulerDecisionEvent extends BaseEvent {
  type: 'scheduler.decision';
  mode: string;
  harness: string;
  window_key: string;
  decision: boolean;
  usage: number;
  t_until_reset: number;
  t_period: number;
  threshold: number;
  rationale: string;
  kicked_ticket_id?: string | null;
}

export interface UsageResetEvent extends BaseEvent {
  type: 'usage.reset';
  harness: string;
  prev_pct: number;
  curr_pct: number;
}

/** Content-bearing conversation block. `action` distinguishes an immutable append from a live
 * trailing-block update. The store appends/replaces the block in the named conversation's
 * transcript slice. `block` is left opaque here — its shape is the transcript DTO, owned above the
 * transport seam. */
export interface ConversationBlockEvent extends BaseEvent {
  type: 'conversation.block';
  conversation_id: string;
  action: 'block-appended' | 'block-updated';
  block: Record<string, unknown>;
}

/** Discriminated union of every server-pushed inner event. `type` is the discriminant. */
export type BusEvent =
  | HeartbeatEvent
  | SummaryEvent
  | QuestionEvent
  | EscalationEvent
  | StatusChangeEvent
  | ErrorEvent
  | CommandEvent
  | StateSnapshotEvent
  | PresenceEvent
  | SchedulerModeEvent
  | SchedulerDecisionEvent
  | UsageResetEvent
  | ConversationBlockEvent;

/** The string literal `type` discriminant of every {@link BusEvent}. */
export type BusEventType = BusEvent['type'];

// === Filter ==================================================================

/** Server-applied subscription filter. Fields compose with AND; an absent field matches any. The
 * broker applies this before fanout. Mirrors Python `EventFilter`. */
export interface EventFilter {
  role?: Role;
  ticket_id?: string;
  type?: BusEventType;
  entity?: Entity;
  target_worker?: string;
  kind?: string;
}

// === Envelope bodies =========================================================

/** First message a client sends after connect. The server replies with an {@link AckMessage}
 * (`kind: 'subscribed'`) on success or an {@link ErrMessage} (`code: 'protocol_version_mismatch'`)
 * on version disagreement. `client_id` is stable across reconnects so the supervisor can resume
 * RPC/presence state. */
export interface HelloBody {
  protocol_version: number;
  client_kind: ClientKind;
  client_id: string;
  since_id?: number | null;
}

/** Subscribe arguments. `since_id=N` replays every persisted event with id > N before the live
 * tail, terminated by an {@link AckMessage} (`kind: 'replay_done'`). `presence_retain` makes the
 * current presence sticky-retained to late subscribers. */
export interface SubArgs {
  filter: EventFilter;
  since_id?: number | null;
  presence_retain: boolean;
}

/** RPC arguments. `target` is the method name; `body` its params. Mirrors Python `RpcArgs`. */
export interface RpcArgs {
  target: string;
  body: Record<string, unknown>;
  timeout_s: number;
}

export interface AckBody {
  kind: 'subscribed' | 'replay_done' | 'rpc_reply' | 'pong';
  watermark?: number | null;
  result?: Record<string, unknown> | null;
}

export interface ErrBody {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

/** Per-client signal on connect/reconnect — distinct from the broadcast {@link PresenceEvent}.
 * Names the entities whose state is most likely stale for the joining client. Not persisted. */
export interface WakeBody {
  client_id: string;
  reason: 'connect' | 'reconnect';
  fresh_state_hints: Entity[];
}

// === Wire envelope (client <-> service; discriminated by `op`) ================

interface BaseMessage {
  schema_version: number;
  correlation_id: string;
}

export interface HelloMessage extends BaseMessage {
  op: 'hello';
  body: HelloBody;
}

export interface PubMessage extends BaseMessage {
  op: 'pub';
  event: BusEvent;
}

export interface SubMessage extends BaseMessage {
  op: 'sub';
  args: SubArgs;
}

export interface RpcMessage extends BaseMessage {
  op: 'rpc';
  args: RpcArgs;
}

export interface AckMessage extends BaseMessage {
  op: 'ack';
  body: AckBody;
}

export interface ErrMessage extends BaseMessage {
  op: 'err';
  body: ErrBody;
}

export interface WakeMessage extends BaseMessage {
  op: 'wake';
  body: WakeBody;
}

/** Discriminated union of every wire envelope. `op` is the discriminant. JSON-lines framed on the
 * socket (one envelope per `\n`-terminated line); the framing itself lives in C2. */
export type WireMessage =
  | HelloMessage
  | PubMessage
  | SubMessage
  | RpcMessage
  | AckMessage
  | ErrMessage
  | WakeMessage;

/** The string literal `op` discriminant of every {@link WireMessage}. */
export type WireOp = WireMessage['op'];

// === Wire constants ==========================================================
// Mirrors the Python `=== Wire constants ===` block. C2 resolves the socket path from the runtime
// subdir + basename; the timeouts seed the real client's RPC/handshake deadlines.

export const SOCKET_RUNTIME_SUBDIR = 'murder';
export const SOCKET_BASENAME = 'bus.sock';

export const DEFAULT_RPC_TIMEOUT_S = 30.0;
export const DEFAULT_HEARTBEAT_INTERVAL_S = 5.0;
export const DEFAULT_LEASE_TTL_S = 30.0;
export const DEFAULT_MAX_COMMAND_ATTEMPTS = 3;
export const COMMAND_REAPER_INTERVAL_S = 5.0;

export const PRESENCE_DISCONNECT_DEBOUNCE_S = 30.0;
/** Only these client kinds count toward {@link PresenceEvent}.user_count. */
export const PRESENCE_USER_KINDS: readonly ClientKind[] = ['tui', 'web'];

export const SUBSCRIBER_QUEUE_DEFAULT = 1024;
export const IDEMPOTENCY_WINDOW_S = 60.0;
