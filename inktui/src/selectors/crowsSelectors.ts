/**
 * Crows view-models — type-grouped presentation for the Crows panel (panel 0, C9).
 *
 * Rule 2 in action: ALL grouping, ordering, and display-formatting live here, never in the
 * component. The CrowsPanel receives a `CrowsView` with pre-built sections; it does zero role
 * inspection, zero sorting, and zero label construction.
 *
 * The four-group ordering follows the interaction spec (Approach › Crows panel (0)):
 *   collaborator → planning agents → rogue crows → ticket crows
 *
 * Role→group mapping (from `murder/bus/protocol.py` `Role` enum + Ink spec):
 *   'collaborator'                                    → collaborator
 *   'planner'                                         → planners
 *   'crow' with ticketId === null                     → rogue crows
 *   'crow' with ticketId !== null                     → ticket crows
 *   'planning_handler' | 'crow_handler' | 'notetaker' → excluded (infrastructure/handler roles)
 *
 * minimized vs maximized:
 *   `expanded === false` (minimized): one short line per crow (name + status).
 *   `expanded === true`  (maximized): two lines per crow (name+status, then harness · model).
 * The component owns the `expanded` boolean as `useState`; the selector produces both shapes
 * for every row so the component can paint either without re-filtering.
 *
 * Two layers, deliberately (mirrors rosterSelectors.ts):
 *  - **Pure transforms** (`selectCrowsView`) — no React, unit-testable in isolation.
 *  - **A `useMemo` hook** (`useCrowsView`) — component-facing, memoises on slice identity.
 */

import { useMemo } from 'react';
import type { RosterRow, RosterState } from '../store/roster/rosterSlice.js';
import { classifyCrowHealth, type Health } from './crowHealthSelectors.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** The four crow groups in spec order. */
export type CrowGroup = 'collaborator' | 'planners' | 'rogue' | 'ticket';

/** Display label for each group header. */
export const CROW_GROUP_LABEL: Readonly<Record<CrowGroup, string>> = {
  collaborator: 'Collaborator',
  planners: 'Planning Agents',
  rogue: 'Rogue Crows',
  ticket: 'Ticket Crows',
};

/** Spec-defined group order (collaborator → planners → rogue → ticket). */
const GROUP_ORDER: readonly CrowGroup[] = ['collaborator', 'planners', 'rogue', 'ticket'];

/** Column width budget for the model cell (matches rosterSelectors). */
const MODEL_WIDTH = 18;

/**
 * One crow row as the CrowsPanel paints it — display-ready in both minimized and maximized
 * views. The component picks the fields it needs based on its current `expanded` state.
 */
export interface CrowRowView {
  readonly agentId: string;
  /** Display name: session name or agentId fallback. */
  readonly name: string;
  readonly status: string;
  /** Harness, or `'—'` when absent. Used in the maximized second line. */
  readonly harness: string;
  /** Model, basename-only and truncated. Used in the maximized second line. */
  readonly model: string;
  /**
   * Client-side health for the crow's left-edge marker (ported from Textual `crow_health.py`).
   * Derived from `status` today; the escalation/stuck branches are dormant until the wire grows
   * `open_escalations`/`max_severity`/`last_seen` (see `crowHealthSelectors.ts` DATA-SHAPE GAP).
   */
  readonly health: Health;
}

/** One section as the component paints it: a header label + the rows under it. */
export interface CrowSection {
  readonly group: CrowGroup;
  readonly label: string;
  readonly rows: readonly CrowRowView[];
}

/**
 * The whole crows view: ordered sections (only non-empty sections are included) plus status
 * flags from the slice so the component can branch on loading/error chrome without extra reads.
 */
export interface CrowsView {
  readonly sections: readonly CrowSection[];
  readonly status: RosterState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Display ordering by liveness (mirrors rosterSelectors STATUS_RANK). */
const STATUS_RANK: Readonly<Record<string, number>> = {
  escalating: 0,
  blocked: 1,
  running: 2,
  idle: 3,
  failed: 4,
};
const STATUS_RANK_FALLBACK = 99;

function truncate(text: string, width: number): string {
  return text.length <= width ? text : `${text.slice(0, width - 1)}…`;
}

function modelBasename(model: string | null): string {
  const raw = (model ?? '').trim();
  if (raw === '') return '—';
  const slash = raw.lastIndexOf('/');
  const base = slash === -1 ? raw : raw.slice(slash + 1);
  return truncate(base, MODEL_WIDTH);
}

/**
 * Classify a `RosterRow.role` string into one of the four display groups, or `null` to exclude
 * internal/infrastructure roles.
 *
 * Role→group mapping (from `murder/app/tui/chat_target_cycle.py` + `murder/bus/protocol.py`):
 *   'collaborator'    → collaborator
 *   'planner'         → planners   (user-facing planning agent)
 *   'crow' + !ticketId → rogue     (rogue crow: no ticket assigned)
 *   'crow' + ticketId  → ticket    (ticket crow: assigned to a ticket)
 *
 * Excluded (infrastructure/handler roles, never user-facing in the panel):
 *   'planning_handler' — the handler-process for planners, not a chat participant
 *                        (`chat_target_cycle.py` includes only role==='planner', not this).
 *   'crow_handler'     — parallel infra role to planning_handler.
 *   'notetaker'        — internal capture agent.
 *   any unknown role   — dropped silently (forward-compatibility).
 *
 * Rogue vs ticket split CONTRACT ASSUMPTION: rogue crows are expected to have no `ticket_id`
 * (they are spawned without a ticket via `crow.spawn_rogue`). This is a deliberate improvement
 * over the old roster.py `_is_rogue_entry` session-name-marker parsing — `ticketId` is the
 * correct discriminant per the architecture (no stringly-typed anti-patterns). If the service
 * ever assigns a ticket_id to a rogue crow, it would appear in "ticket crows" instead; document
 * this assumption for C10 (discriminated-union agent identity is built from `role` + `ticketId`).
 */
function rowToGroup(row: RosterRow): CrowGroup | null {
  switch (row.role) {
    case 'collaborator':
      return 'collaborator';
    case 'planner':
      return 'planners';
    case 'crow':
      return row.ticketId === null ? 'rogue' : 'ticket';
    default:
      // 'planning_handler' | 'crow_handler' | 'notetaker' | any unknown → exclude.
      return null;
  }
}

function toRowView(row: RosterRow): CrowRowView {
  return {
    agentId: row.agentId,
    name: row.session ?? row.agentId,
    status: row.status,
    harness: row.harness ?? '—',
    model: modelBasename(row.model),
    // Status-only today: the wire (`RosterRow`/`CrowSessionDto`) carries no escalation count,
    // severity, or heartbeat, so escalation-RED and stuck-YELLOW pass their defaults and stay
    // dormant until the service adds those fields. See crowHealthSelectors.ts DATA-SHAPE GAP.
    health: classifyCrowHealth({ status: row.status }),
  };
}

function byStatusThenId(a: RosterRow, b: RosterRow): number {
  const rankA = STATUS_RANK[a.status] ?? STATUS_RANK_FALLBACK;
  const rankB = STATUS_RANK[b.status] ?? STATUS_RANK_FALLBACK;
  return rankA - rankB || a.agentId.localeCompare(b.agentId);
}

// ---------------------------------------------------------------------------
// Pure transform
// ---------------------------------------------------------------------------

/**
 * The pure view-model transform — the testable core. Groups rows by type in spec order,
 * sorts within each group by status then id. Omits empty groups from the output. Omits
 * internal/infrastructure roles (`notetaker`, `crow_handler`, etc.).
 *
 * Same input → same output; no React, no store, no bus.
 */
export function selectCrowsView(state: RosterState): CrowsView {
  // Sort a copy (never mutate the readonly slice) before grouping so within-group order is stable.
  const sorted = [...state.rows].sort(byStatusThenId);

  // Accumulate rows per group.
  const grouped = new Map<CrowGroup, RosterRow[]>(GROUP_ORDER.map((g) => [g, []]));
  for (const row of sorted) {
    const group = rowToGroup(row);
    if (group !== null) {
      grouped.get(group)?.push(row);
    }
  }

  // Build sections in spec order; omit empty groups.
  const sections: CrowSection[] = [];
  for (const group of GROUP_ORDER) {
    const rows = grouped.get(group);
    if (rows !== undefined && rows.length > 0) {
      sections.push({
        group,
        label: CROW_GROUP_LABEL[group],
        rows: rows.map(toRowView),
      });
    }
  }

  const isEmpty = sections.length === 0;
  return {
    sections,
    status: state.status,
    error: state.error,
    isEmpty,
  };
}

/**
 * Component-facing hook: memoises {@link selectCrowsView} on the slice identity. Because the
 * store ref-swaps the whole `roster` slice only on change, `state` is referentially stable
 * between unrelated re-renders, so this re-groups only when the roster actually changed.
 *
 * Usage: `const view = useCrowsView(useAppStore((s) => s.roster));`
 */
export function useCrowsView(state: RosterState): CrowsView {
  return useMemo(() => selectCrowsView(state), [state]);
}
