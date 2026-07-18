/**
 * Roster actions — the *only* code that calls the bus for crow data (rule 3).
 *
 * An action is a closure over the injected {@link BusClient} and the store's `set`. It issues one
 * query, projects the wire DTO into the slice's presentation-free {@link RosterRow}s, and ref-swaps
 * *only* the `roster` slice. Components and selectors never reach the bus — they dispatch these
 * actions (exposed off the store handle) or read the slice. This file is the seam a future
 * web/phone client reuses unchanged: no Ink, no terminal, no socket — just `BusClient` + the store.
 *
 * The loading→ready/error lifecycle + ref-swap-only-this-key mechanics now come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js` — this file supplies only the three
 * per-domain pieces: the query name (+ its reply type, declared below), and the DTO→rows
 * `project` fn. That projection is the divergence injection point; the generic never special-cases
 * a domain.
 *
 * Copy this file to add slice X: swap the query name + its reply shape (the `declare module` block),
 * swap the projection, and pass X's slice key + method + project to `createRefreshAction`. The
 * augmentation block is how a feature declares its typed result for a generated query name.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { submitCommand } from '../commandSubmit.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { RosterRow } from './rosterSlice.js';

/**
 * The crow-roster query and its reply shape, declared here via TypeScript declaration merging.
 * The query name comes from the generated application protocol; the feature owns its result shape.
 *
 * `roster.get` is adapted by the application gateway to the internal roster snapshot handler.
 */
declare module '../../bus/BusClient.js' {
  interface QueryMethods {
    /** Fetch the full crow roster. Re-pulled on each `agent`-entity `state.snapshot`. */
    'roster.get': { params: Record<string, never>; result: CrowSnapshotReply };
  }
}

/**
 * The `roster.get` reply, mirroring the service's `CrowSnapshot` DTO (`murder/app/service/
 * client_api.py`). Only the fields the roster projects are typed; the wire may carry more. Optional
 * fields are `?: T | null` to match the Python `T | None` columns exactly.
 */
export interface CrowSnapshotReply {
  sessions: readonly CrowSessionDto[];
  invalidation_key: string;
}

/**
 * One session row as it crosses the wire (Python `CrowSessionSummary`). Presentation-free.
 * `role` mirrors `murder/bus/protocol.py`'s `Role` enum (`'collaborator' | 'planner' | 'crow' |
 * …`). The slice stores it as a raw string; `crowsSelectors.ts` (C9) uses it for type-grouping.
 *
 * Rich fields added from Python `CrowSessionSummary` (client_api.py:102-116):
 * - `last_seen` / `started_at`: ISO-8601 datetime strings (Python `datetime.isoformat()`), or null.
 * - `open_escalations`: count of open escalations linked to this crow's ticket (default 0).
 * - `max_severity`: max severity across open escalations (default 0).
 * - `ticket_status`: the ticket's current status string, or null.
 * - `worktree_path`: filesystem path of the crow's worktree, or null.
 */
export interface CrowSessionDto {
  agent_id: string;
  role: string;
  ticket_id?: string | null;
  ticket_title?: string | null;
  harness?: string | null;
  model?: string | null;
  status: string;
  session_name?: string | null;
  /** Durable HarnessSessionRecord UUID; absent for transitional legacy agents. */
  session_id?: string | null;
  /** ISO-8601 string from Python datetime.isoformat(), or null. Used for stuck-heartbeat detection. */
  last_seen?: string | null;
  /** ISO-8601 string from Python datetime.isoformat(), or null. */
  started_at?: string | null;
  /** The ticket's current status string (mirrors Python `TicketStatus`), or null. */
  ticket_status?: string | null;
  /** Filesystem path of the crow's worktree, or null. */
  worktree_path?: string | null;
  /** Count of open escalations linked to this crow's ticket. Python default 0. */
  open_escalations?: number;
  /** Max severity across this crow's open escalations. Python default 0. */
  max_severity?: number;
}

/** Project one wire session into the slice's row. Pure: the single place the DTO→domain mapping
 * lives, so a field rename on the wire is fixed once. No filtering/sorting — that is the selector. */
function toRosterRow(session: CrowSessionDto): RosterRow {
  return {
    agentId: session.agent_id,
    role: session.role,
    ticketId: session.ticket_id ?? null,
    ticketTitle: session.ticket_title ?? null,
    harness: session.harness ?? null,
    model: session.model ?? null,
    status: session.status,
    session: session.session_name ?? null,
    ...(session.session_id == null ? {} : { sessionId: session.session_id }),
    worktreePath: session.worktree_path ?? null,
    lastSeen: session.last_seen ?? null,
    openEscalations: session.open_escalations ?? 0,
    maxSeverity: session.max_severity ?? 0,
  };
}

/**
 * The roster's actions, bound to one `BusClient` + store handle. Returned to `../store.ts`, which
 * hangs them off the store so components dispatch `store.getState().actions.roster.refresh()` (or,
 * in React, a selector-picked action). Held in a `RosterActions` object so the slice's verbs are
 * discoverable in one place and copyable as a unit.
 */
export interface RosterActions {
  /**
   * Re-pull the roster and ref-swap *only* the `roster` slice. The sole bus caller for crow data.
   * Idempotent and safe to call on every matching `state.snapshot`; concurrent calls are last-write
   * (the latest reply wins), which is correct for a full-snapshot read. Rejections land in
   * `roster.error` and flip `status` to `'error'` — never thrown past the action, so the
   * event-invalidation loop in `../store.ts` stays fire-and-forget.
   */
  refresh(): Promise<void>;
  /**
   * Kill a stuck/wrong-track crow and re-queue its ticket as `ready` in one step (the lifecycle-
   * robustness plan's Objective 1). Submits the `crow.reset` orchestrator command (kills the tmux
   * session, reaps crow + handler, transitions the ticket to ready — NOT failed). Rejects on a
   * failed command — the caller (CrowsPanel's confirm) surfaces the outcome as a toast. The roster
   * row update arrives via the `agent`/`ticket` entity snapshots.
   */
  resetCrow(ticketId: string): Promise<void>;
}

export function createRosterActions(bus: BusClient, store: StoreApi<AppStore>): RosterActions {
  return {
    ...createRefreshAction(bus, store, {
      key: 'roster',
      method: 'roster.get',
      project: (reply) => reply.sessions.map(toRosterRow),
    }),
    async resetCrow(ticketId: string): Promise<void> {
      await submitCommand(bus, 'crow.reset', { ticket_id: ticketId });
    },
  };
}
