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

export const PROTOCOL_VERSION = 5;

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
 * PROTOCOL_VERSION. These are the slice names the store invalidates against.
 *
 * Mirrors the Python `Entity` enum (`murder/bus/protocol.py`) 1:1, including `report` (added to
 * Python in F1; the former C6 forward-declaration is now backed by the real backend enum). */
export type Entity =
  | 'ticket'
  | 'agent'
  | 'plan'
  | 'note'
  | 'report'
  | 'escalation'
  | 'queue_row'
  | 'history'
  | 'transit';

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

/** A free-text working note emitted by a crow via a `>>> NOTE:` marker. Under DB-owns-runtime
 * these land in the events table (audit log), not the ticket `.md`. Carried for union
 * completeness; the store re-pulls the notes slice via the `note` {@link Entity} `state.snapshot`,
 * so this event needs no dedicated handler. Mirrors Python `NoteEvent`. */
export interface NoteEvent extends BaseEvent {
  type: 'note';
  note: string;
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
  /** Reserved forward-compat field (F1, unused now). The contract stays key-only: clients refetch
   * the named slice. A future low-bandwidth mode MAY inline the changed data here to skip the
   * refetch round-trip. Absent on the wire today; do not build a translation layer off it. */
  payload?: Record<string, unknown> | null;
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

/** Completion coordinator verdict for a ticket (peer of {@link SchedulerDecisionEvent}). Mirrors
 * `CompletionVerdictEvent` in `murder/bus/protocol.py`. SERVER-SIDE FORENSIC event: the client does
 * NOT act on it directly — it reads completion via the key-only `state.snapshot` + slice refetch
 * path. Declared here only so the discriminated union parses it. */
export interface CompletionVerdictEvent extends BaseEvent {
  type: 'completion.verdict';
  completed: boolean;
  ticket_failed: boolean;
  failed_checks: string[];
}

/** Rich agent-registry mutation (register / rename / clear / force_stop). Mirrors
 * `AgentLifecycleEvent` in `murder/bus/protocol.py`. SERVER-SIDE FORENSIC event: the client does NOT
 * act on it directly — agent state arrives via the key-only `state.snapshot` path. Declared here
 * only so the discriminated union parses it. */
export interface AgentLifecycleEvent extends BaseEvent {
  type: 'agent.lifecycle';
  op: 'register' | 'rename' | 'clear' | 'force_stop';
  details: Record<string, unknown>;
  reason?: string | null;
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

/** Content-bearing conversation liveness push. Companion to {@link ConversationBlockEvent} on the
 * same additive-event-kind seam (mirrors `ConversationStateEvent` in `murder/bus/protocol.py`):
 * emitted whenever a conversation's parsed harness UI state (`working` / `awaiting_input` /
 * `awaiting_approval`) or its queued-but-undelivered user message changes. The conversations slice
 * stores both per agent so the chat input can render the queued line + awaiting badge live. */
export interface ConversationStateEvent extends BaseEvent {
  type: 'conversation.state';
  conversation_id: string;
  /** Parsed harness UI state at last projection: working | awaiting_input | awaiting_approval. */
  live_state: string | null;
  /** A user message accepted while the harness was busy, held for idle delivery; null when none. */
  queued_message: string | null;
}

/**
 * Raw ANSI frame from tmux for the focused pane. The CLIENT subscribes on `ctrl+y` enter and
 * disposes on exit. CAVEAT — the disposer is LOCAL-only: the wire protocol has no `unsub` op, so
 * the server keeps streaming frames over the multiplexed connection until the whole connection
 * closes; the client simply stops fanning them out (see `UdsBusClient.subscribe`'s disposer). So
 * this is NOT a zero standing cost after exit — the frames keep arriving and are dropped client-
 * side. Closing that gap for real needs a server-side per-subscription teardown op (a `unsub`
 * frame the server honours), which the bus does not yet expose. Track that as the real fix.
 *
 * Mirrors `TmuxFrameEvent` in `murder/bus/protocol.py` — both added in F6, `PROTOCOL_VERSION`
 * bumped to 3 in lockstep across both files.
 *
 * The frame carries the full rendered ANSI output of the pane as a snapshot string (from
 * `tmux capture-pane -e`). The consumer replaces its display on every event (no incremental
 * patching). Ink `<Text>` renders ANSI escape sequences natively.
 *
 * Pane-scoping: subscribe with `agent_id` in the {@link EventFilter} to stream that agent's own
 * tmux session; without it the service falls back to its project session.
 */
export interface TmuxFrameEvent extends BaseEvent {
  type: 'tmux.frame';
  /** The full rendered ANSI content for the focused tmux pane (snapshot, not incremental). */
  frame: string;
}

/** Discriminated union of every server-pushed inner event. `type` is the discriminant. */
export type BusEvent =
  | HeartbeatEvent
  | SummaryEvent
  | QuestionEvent
  | NoteEvent
  | EscalationEvent
  | StatusChangeEvent
  | ErrorEvent
  | CommandEvent
  | StateSnapshotEvent
  | PresenceEvent
  | SchedulerModeEvent
  | SchedulerDecisionEvent
  | CompletionVerdictEvent
  | AgentLifecycleEvent
  | UsageResetEvent
  | ConversationBlockEvent
  | ConversationStateEvent
  | TmuxFrameEvent;

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
  agent_id?: string;
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
  /** Optional: Python `ErrBody.details` defaults to `{}` (`Field(default_factory=dict)`) and is
   * omitted on the wire when empty, so the client must not require it (C-D3). */
  details?: Record<string, unknown>;
}

/** Per-client signal on connect — distinct from the broadcast {@link PresenceEvent}.
 * Names the entities whose state is most likely stale for the joining client. Not persisted. */
export interface WakeBody {
  client_id: string;
  /** Only `'connect'` is ever sent today: the Python server constructs `WakeBody` with
   * `reason="connect"` in exactly one place (`transport_socket.py`) and never `"reconnect"`. The
   * Python type keeps `"reconnect"` as a forward-compat Literal, but the consumer models only what
   * is actually emitted (C-LM-5). Re-add `'reconnect'` here if the server starts sending it. */
  reason: 'connect';
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
