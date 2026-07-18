/**
 * Transport-agnostic client for Murder's service-owned application protocol.
 *
 * Product capability names live in the generated protocol. Feature action modules declaration-
 * merge their parameter and result DTOs into {@link QueryMethods} and {@link CommandMethods}; the
 * socket transport never maps those names back to legacy RPC targets. That compatibility mapping
 * belongs to the service gateway.
 */

import type {
  CommandName,
  ProjectionTopic,
  QueryName,
  TerminalChunk,
  TerminalFrame as TerminalFrameContract,
  TerminalFrameMessage,
} from '../generated/applicationProtocol.js';
import type { BusEvent } from './protocol.js';

export type ApplicationPayload = Record<string, unknown>;

/** Feature-owned query DTO registry. Extended with declaration merging beside each action. */
// biome-ignore lint/suspicious/noEmptyInterface: declaration merging requires an interface.
export interface QueryMethods {}

/** Feature-owned command DTO registry. Extended with declaration merging beside each action. */
// biome-ignore lint/suspicious/noEmptyInterface: declaration merging requires an interface.
export interface CommandMethods {}

/** Only generated public names may enter a feature registry. */
export type QueryMethod = Extract<keyof QueryMethods, QueryName>;
export type CommandMethod = Extract<keyof CommandMethods, CommandName>;
export type QueryParams<M extends QueryMethod> = QueryMethods[M] extends {
  params: infer Params;
}
  ? Params
  : never;
export type QueryResult<M extends QueryMethod> = QueryMethods[M] extends {
  result: infer Result;
}
  ? Result
  : never;
export type CommandParams<M extends CommandMethod> = CommandMethods[M] extends {
  params: infer Params;
}
  ? Params
  : never;
export type CommandResult<M extends CommandMethod> = CommandMethods[M] extends {
  result: infer Result;
}
  ? Result
  : never;

/**
 * Compatibility payload type for projection invalidations and replay items. The application wire
 * intentionally carries an opaque object; existing store projections continue to consume the
 * established DTO union while backend feature contracts are introduced incrementally.
 */
export type BusEventListener = (event: BusEvent) => void;
export type Unsubscribe = () => void;

export type ProjectionTopics = ProjectionTopic | readonly ProjectionTopic[];
export type HydrateSnapshots = Partial<Record<ProjectionTopic, unknown>>;

export interface HydrateReply {
  readonly snapshots: HydrateSnapshots;
  readonly cursor: number | null;
  readonly mode?: 'cold' | 'resume' | 'snapshot_fallback';
  readonly replay?: readonly { readonly seq: number; readonly event: BusEvent }[];
}

export interface HydrateResult extends HydrateReply {
  /** Sends a real application-protocol `unsubscribe` and removes reconnect intent. */
  readonly unsubscribe: Unsubscribe;
}

export type TerminalFrame = TerminalFrameMessage['frame'];
export type TerminalUpdate = TerminalFrameContract | TerminalChunk;
export type TerminalFrameListener = (update: TerminalUpdate) => void;

export interface BusClient {
  query<M extends QueryMethod>(name: M, params: QueryParams<M>): Promise<QueryResult<M>>;

  command<M extends CommandMethod>(name: M, params: CommandParams<M>): Promise<CommandResult<M>>;

  /**
   * Subscribe to projection snapshots plus their resumable invalidation tail. The transport owns
   * the cursor and reattaches on reconnect; callers apply snapshots, then compatibility events.
   */
  hydrate(topics: ProjectionTopics, listener?: BusEventListener): Promise<HydrateResult>;

  /**
   * Attach the replace-frame terminal stream for `sessionId`. The synchronous disposer removes the
   * reconnect intent and sends `terminal.detach` when the stream has reached the server.
   */
  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe;
}
