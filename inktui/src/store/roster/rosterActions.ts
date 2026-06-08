/**
 * Roster actions — the *only* code that calls the bus for crow data (rule 3).
 *
 * An action is a closure over the injected {@link BusClient} and the store's `set`. It issues one
 * RPC, projects the wire DTO into the slice's presentation-free {@link RosterRow}s, and ref-swaps
 * *only* the `roster` slice. Components and selectors never reach the bus — they dispatch these
 * actions (exposed off the store handle) or read the slice. This file is the seam a future
 * web/phone client reuses unchanged: no Ink, no terminal, no socket — just `BusClient` + the store.
 *
 * The loading→ready/error lifecycle + ref-swap-only-this-key mechanics now come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js` — this file supplies only the three
 * per-domain pieces: the RPC method (+ its reply type, declared below), and the DTO→rows
 * `project` fn. That projection is the divergence injection point; the generic never special-cases
 * a domain.
 *
 * Copy this file to add slice X: swap the RPC method + its reply shape (the `declare module` block),
 * swap the projection, and pass X's slice key + method + project to `createRefreshAction`. The
 * augmentation block is how a read RPC not yet on the shared {@link RpcMethods} registry is declared
 * without editing the frozen C1 bus files — see its comment.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { RosterRow } from './rosterSlice.js';

/**
 * The crow-roster read RPC and its reply shape, declared here via TypeScript declaration merging
 * rather than by editing `src/bus/BusClient.ts` (frozen at C1/C2). The registry was designed to be
 * extended "a line per method as the service exposes it"; doing it from the consuming slice keeps
 * the bus seam byte-identical while still giving `bus.rpc('crow.get_snapshot', …)` full type safety.
 *
 * NOTE FOR THE SERVICE: `crow.get_snapshot` is **not yet on the live bus** — it is the RPC the
 * Python `RuntimeClient.get_crow_snapshot()` exposes locally, modeled here per the Bus contract's
 * "view → service = RPC methods" rule (namespaced `domain.verb`, like `ticket.quick_kick`). When
 * service B13 lands the read surface, confirm this name/shape or update both sides in lockstep.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full crow roster. Re-pulled on each `agent`-entity `state.snapshot`. */
    'crow.get_snapshot': { params: Record<string, never>; result: CrowSnapshotReply };
  }
}

/**
 * The `crow.get_snapshot` reply, mirroring the service's `CrowSnapshot` DTO (`murder/app/service/
 * client_api.py`). Only the fields the roster projects are typed; the wire may carry more. Optional
 * fields are `?: T | null` to match the Python `T | None` columns exactly.
 */
export interface CrowSnapshotReply {
  sessions: readonly CrowSessionDto[];
  invalidation_key: string;
}

/** One session row as it crosses the wire (Python `CrowSessionSummary`). Presentation-free. */
export interface CrowSessionDto {
  agent_id: string;
  ticket_id?: string | null;
  ticket_title?: string | null;
  harness?: string | null;
  model?: string | null;
  status: string;
  session_name?: string | null;
}

/** Project one wire session into the slice's row. Pure: the single place the DTO→domain mapping
 * lives, so a field rename on the wire is fixed once. No filtering/sorting — that is the selector. */
function toRosterRow(session: CrowSessionDto): RosterRow {
  return {
    agentId: session.agent_id,
    ticketId: session.ticket_id ?? null,
    ticketTitle: session.ticket_title ?? null,
    harness: session.harness ?? null,
    model: session.model ?? null,
    status: session.status,
    session: session.session_name ?? null,
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
}

export function createRosterActions(bus: BusClient, store: StoreApi<AppStore>): RosterActions {
  return createRefreshAction(bus, store, {
    key: 'roster',
    method: 'crow.get_snapshot',
    project: (reply) => reply.sessions.map(toRosterRow),
  });
}
