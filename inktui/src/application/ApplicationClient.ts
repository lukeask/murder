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
  ProjectionSnapshot,
  ProjectionTopic,
  QueryMethod,
  QueryParams,
  QueryResult,
  TerminalChunk,
  TerminalFrame as TerminalFrameContract,
  TerminalFrameMessage,
  TerminalKeyframe,
  TerminalStreamGap,
} from '../generated/applicationProtocol.js';

/** Fenced raw-terminal writer context. A new stream identity is issued whenever ownership is
 * (re)acquired, so stale sequence numbers cannot be replayed under a new lease. */
export interface TerminalInputLease {
  readonly streamId: string;
  readonly sessionId: string;
  readonly leaseId: string;
  readonly fence: number;
}

export interface TerminalInputBatch extends TerminalInputLease {
  readonly inputSequence: number;
  readonly data: string;
}

export type ApplicationPayload = any;
export type { CommandMethods, QueryMethods } from '../generated/applicationProtocol.js';
export type { CommandMethod, CommandParams, CommandResult, QueryMethod, QueryParams, QueryResult };

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
/**
 * Raw terminal stream delivery. Chunks remain base64 at this boundary: the
 * terminal parser, not this transport client, owns byte decoding.  The legacy
 * UTF-8 `TerminalFrame` remains a replace-state compatibility variant.
 */
export type TerminalUpdate =
  | TerminalFrameContract
  | TerminalKeyframe
  | TerminalChunk
  | TerminalStreamGap
  | { readonly type: 'terminal.resynced'; readonly keyframe: TerminalKeyframe };
export type TerminalFrameListener = (update: TerminalUpdate) => void;
export type TerminalAttachMode = 'raw' | 'replace';

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
   * Attach a terminal stream for `sessionId`. Native surfaces use ordered raw VT; legacy
   * transcript consumers can explicitly request complete replacement frames during migration.
   */
  attachTerminal(
    sessionId: string | null,
    listener: TerminalFrameListener,
    mode?: TerminalAttachMode,
  ): Unsubscribe;

  /** Acquire the service's raw-terminal writer lease for a session. */
  openTerminalInput(sessionId: string): Promise<TerminalInputLease>;
  /** Renew an acquired writer lease without changing its input-stream sequence space. */
  renewTerminalInput(lease: TerminalInputLease): Promise<TerminalInputLease>;
  /** Release a writer lease when the interactive surface loses ownership. */
  closeTerminalInput(lease: TerminalInputLease): Promise<void>;
  /** Send one base64-encoded raw-byte batch without a request/reply round trip. */
  sendTerminalInput(batch: TerminalInputBatch): boolean;
  /** Reports a terminal stream failure (stale lease/fence, sequence gap, or writer failure). */
  watchTerminalInput(streamId: string, listener: (error: Error) => void): Unsubscribe;
}
