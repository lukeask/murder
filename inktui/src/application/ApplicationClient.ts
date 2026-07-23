/**
 * Transport-agnostic client for Murder's service-owned application protocol.
 *
 * Product capability names and request/result DTOs are generated from the server-side
 * Pydantic operation registry. The socket transport never maps those names back to legacy RPC
 * targets; that compatibility mapping belongs to the service gateway.
 */

import type {
  CommandMethod,
  CommandParams,
  CommandResult,
  ProjectionTopic,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalChunk,
  TerminalFrame as TerminalFrameContract,
  TerminalFrameMessage,
  ProjectionSnapshot,
} from '../generated/applicationProtocol.js';

export type ApplicationPayload = any;
export type { CommandMethod, CommandParams, CommandResult, QueryMethod, QueryParams, QueryResult };
export type { CommandMethods, QueryMethods } from '../generated/applicationProtocol.js';

export interface ProjectionInvalidation {
  readonly type: 'projection.invalidate';
  readonly projection: ProjectionTopic;
  readonly subject_key: string;
  readonly generation: number;
  readonly source_fact_id?: string | null;
}
export type ProjectionInvalidationListener = (invalidation: ProjectionInvalidation) => void;
/** Delivered for every `subscription.ready` that carries projection snapshots (cold boot and
 * reconnect `snapshot_fallback`). Distinct from {@link ProjectionInvalidationListener}, which only
 * sees the invalidation tail. */
export type ProjectionSnapshotListener = (reply: HydrateReply) => void;
export type Unsubscribe = () => void;

export type ProjectionTopics = ProjectionTopic | readonly ProjectionTopic[];
/** Typed snapshots supplied when a projection subscription becomes ready. */
export type HydrateSnapshots = ProjectionSnapshot;

export interface HydrateReply {
  readonly snapshots: HydrateSnapshots;
  readonly cursor: number | null;
  readonly mode?: 'cold' | 'resume' | 'snapshot_fallback';
}

export interface HydrateResult extends HydrateReply {
  /** Sends a real application-protocol `unsubscribe` and removes reconnect intent. */
  readonly unsubscribe: Unsubscribe;
}

export type TerminalFrame = TerminalFrameMessage['frame'];
export type TerminalUpdate = TerminalFrameContract | TerminalChunk;
export type TerminalFrameListener = (update: TerminalUpdate) => void;

export interface ApplicationClient {
  query<M extends QueryMethod>(name: M, params: QueryParams<M>): Promise<QueryResult<M>>;

  command<M extends CommandMethod>(name: M, params: CommandParams<M>): Promise<CommandResult<M>>;

  /**
   * Subscribe to typed projection snapshots plus their resumable invalidation tail. The transport
   * owns the cursor and reattaches on reconnect.
   *
   * @param since - Resume cursor for the projection subscription. `null` forces a cold subscribe
   *   (omit cursor). When omitted, transports that have completed `server.hello` default to
   *   {@link ServerHello.projection_cursor}.
   * @param snapshotListener - Invoked whenever a ready frame carries snapshots, including
   *   reconnect `snapshot_fallback` after the initial hydrate promise has already settled.
   */
  hydrate(
    topics: ProjectionTopics,
    invalidationListener?: ProjectionInvalidationListener,
    since?: number | null,
    snapshotListener?: ProjectionSnapshotListener,
  ): Promise<HydrateResult>;

  /**
   * Attach the replace-frame terminal stream for `sessionId`. The synchronous disposer removes the
   * reconnect intent and sends `terminal.detach` when the stream has reached the server.
   */
  attachTerminal(sessionId: string | null, listener: TerminalFrameListener): Unsubscribe;
}
