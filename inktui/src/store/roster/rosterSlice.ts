/**
 * Roster slice — the reference domain slice for the whole store layer.
 *
 * A slice is one domain's state plus the actions that mutate it. This file owns the *state shape*
 * and the slice factory; the bus-calling work lives in {@link ./rosterActions.js} so rule 3 (actions
 * are the only view→bus path) is enforced by file boundary, not by convention. Presentation
 * (sort/truncate/columns) is deliberately absent — that is the selector's job (rule 2). What lands
 * here is domain data only, exactly as the service delivers it, so the slice stays reusable by a
 * future React-DOM client (rule 4).
 *
 * The shared `{ rows, status, error }` mechanics now come from the generic {@link ListState} +
 * {@link createListSlice} factory in `../listSlice.js` — this file is a thin shell over it. Only
 * the row type, the slice key (`roster`), and the invalidating entity are domain-specific here.
 *
 * Copy this file to add slice X: rename `RosterRow`→`XRow` and its fields for X's DTO, point
 * `INVALIDATING_ENTITY` at X's {@link Entity} key, and pass X's key to `createListSlice`. The
 * action (`./rosterActions.ts`) and selector (`../selectors/rosterSelectors.ts`) follow the same
 * copy recipe; the loading/error/ref-swap mechanics are inherited from the factory, not re-derived.
 */

import type { Entity } from '../../bus/protocol.js';
import { createListSlice, initialListState, type ListState } from '../listSlice.js';

/**
 * One crow as the roster cares about it — a faithful, presentation-free projection of the service's
 * crow-session DTO (Python `CrowSessionSummary`). No sort key, no truncated label, no column tuple:
 * those are the selector's output, never the store's (rule 2). `null` mirrors the wire's optional
 * fields so a missing value is explicit, never an empty-string sentinel.
 *
 * `role` mirrors the Python `Role` enum string (`'collaborator' | 'planner' | 'crow' | …`); it is
 * stored as a raw string so the slice stays wire-faithful and a future consumer (C10 discriminated-
 * union agent identity) can narrow it without the slice pre-judging the shape.
 */
export interface RosterRow {
  readonly agentId: string;
  readonly role: string;
  readonly ticketId: string | null;
  readonly ticketTitle: string | null;
  readonly harness: string | null;
  readonly model: string | null;
  readonly status: string;
  readonly session: string | null;
  /**
   * ISO-8601 heartbeat timestamp, or null when not available. Used by `crowsSelectors.ts` to
   * compute the stuck-but-alive (YELLOW) health branch via `isStuck`. Python serialises
   * `datetime` fields as `datetime.isoformat()`, so this is always an ISO-8601 string on the wire.
   * Optional on `RosterRow` so existing test factories don't require the field.
   */
  readonly lastSeen?: string | null;
  /**
   * Count of open escalations linked to this crow's ticket. Fed from Python
   * `CrowSessionSummary.open_escalations` (default 0). Drives the escalation-RED health branch.
   * Optional on `RosterRow` so existing test factories don't require the field.
   */
  readonly openEscalations?: number;
  /**
   * Max severity across this crow's open escalations. Fed from Python
   * `CrowSessionSummary.max_severity` (default 0). Drives the severity-RED health branch.
   * Optional on `RosterRow` so existing test factories don't require the field.
   */
  readonly maxSeverity?: number;
}

/**
 * The roster slice's state — the shared {@link ListState} shape specialized to {@link RosterRow}.
 * `rows` is the domain data; `status` makes the load lifecycle explicit so a component can
 * distinguish "not fetched yet" from "fetched, empty". Selectors read `RosterState['status']`, so
 * the union is part of the contract (it stays `'idle' | 'loading' | 'ready' | 'error'`).
 */
export type RosterState = ListState<RosterRow>;

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. The service emits
 * key-only change events naming the entity that changed; the store re-pulls *only* the slice whose
 * `INVALIDATING_ENTITY` matches (see `../store.ts`). Crows are `agent`-keyed on the wire.
 */
export const ROSTER_INVALIDATING_ENTITY: Entity = 'agent';

/**
 * A SECOND {@link Entity} key that invalidates this slice. Each crow's `state.crow_snapshot` reply
 * carries escalation counts (`open_escalations` / `max_severity`, JOINed in the Python DTO), so an
 * `escalation` change must re-pull the roster to keep those counts fresh — an escalation can be
 * created or resolved without a coincident `agent` change, which would otherwise leave them stale.
 */
export const ROSTER_ESCALATION_INVALIDATING_ENTITY: Entity = 'escalation';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialRosterState: RosterState = initialListState<RosterRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `roster` key, built from the
 * shared {@link createListSlice}. It contributes only its own key; `../store.ts` composes it with
 * sibling slices into the one root store. No bus dependency here (rule 4) — mutation is the
 * action layer's job.
 */
export const createRosterSlice = createListSlice('roster', initialRosterState);
