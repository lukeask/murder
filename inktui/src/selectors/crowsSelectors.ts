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
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { RosterRow, RosterState } from '../store/roster/rosterSlice.js';
import {
  deriveAgentIdentity,
  hasTicket,
  isDefaultFavorited,
  stripSessionPrefix,
} from './agentIdentity.js';
import { classifyCrowHealth, type Health, isStuck } from './crowHealthSelectors.js';
import { isFavorited, stableSortStarredFirst } from './favoritesSelectors.js';

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

/** An empty favorite set — the defaults-only fallback when no prefs slice is supplied. */
const NO_FAVORITES: FavoritesState = { ids: new Set<string>(), status: 'idle', error: null };

/** Column width budget for the model cell (matches rosterSelectors). */
const MODEL_WIDTH = 18;

/**
 * One crow row as the CrowsPanel paints it — display-ready in both minimized and maximized
 * views. The component picks the fields it needs based on its current `expanded` state.
 */
export interface CrowRowView {
  readonly agentId: string;
  /** Display name: the session name with its `murder_<repo>_<role…>_` prefix stripped (item 11),
   * or the agentId when no session is set. */
  readonly name: string;
  /** True when this crow is favorited (explicit star OR kind-default) — drives the `★ ` glyph and
   * the starred-first sort within the group (item 9d). */
  readonly favorited: boolean;
  readonly status: string;
  /** Harness, or `'—'` when absent. Used in the maximized second line. */
  readonly harness: string;
  /** Model, basename-only and truncated. Used in the maximized second line. */
  readonly model: string;
  /**
   * Client-side health for the crow's left-edge marker (ported from Textual `crow_health.py`).
   * Derived from `status`, `openEscalations`, `maxSeverity`, and the 60s stuck-heartbeat rule
   * using `last_seen`. All four branches of `classifyCrowHealth` are now live: RED (escalation /
   * severity / red-status), YELLOW (stuck), GREEN (running/idle), NEUTRAL (done/unknown).
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
 * Role→group mapping (from `murder/bus/protocol.py`):
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
      // `hasTicket` (not `=== null`) so a rogue's empty-string `ticket_id` still reads as no-ticket
      // → rogue group (item 9a; see agentIdentity.hasTicket for the empty-vs-null backend story).
      return hasTicket(row.ticketId) ? 'ticket' : 'rogue';
    default:
      // 'planning_handler' | 'crow_handler' | 'notetaker' | any unknown → exclude.
      return null;
  }
}

function toRowView(row: RosterRow, nowMs: number, favorited: boolean): CrowRowView {
  // Parse the ISO-8601 last_seen into milliseconds; null if absent or unparseable.
  //
  // Python `read_model.py` uses `datetime.utcnow()` — naive UTC datetimes — so `.isoformat()`
  // produces a suffix-less string (e.g. "2026-06-09T04:56:09.123456"). `Date.parse` treats a
  // no-offset ISO string as LOCAL time (ES2015+), which would make nowMs − lastSeenMs wrong by
  // the local TZ offset. Normalise: append "Z" when no tz-offset marker (+/-/Z) is present so
  // that `Date.parse` always interprets the value as UTC, matching Python naive-UTC convention.
  const rawLastSeen = row.lastSeen ?? null;
  const lastSeenMs: number | null =
    rawLastSeen !== null
      ? (() => {
          const normalised = /[+Z]/.test(rawLastSeen) ? rawLastSeen : `${rawLastSeen}Z`;
          const ms = Date.parse(normalised);
          return Number.isNaN(ms) ? null : ms;
        })()
      : null;

  const stuck = isStuck({ status: row.status, lastSeenMs, nowMs });

  return {
    agentId: row.agentId,
    name: row.session !== null ? stripSessionPrefix(row.session) : row.agentId,
    favorited,
    status: row.status,
    harness: row.harness ?? '—',
    model: modelBasename(row.model),
    health: classifyCrowHealth({
      status: row.status,
      openEscalations: row.openEscalations ?? 0,
      maxSeverity: row.maxSeverity ?? 0,
      stuck,
    }),
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
 * sorts within each group by status then id, then re-partitions favorited crows to the top of
 * their group (item 9d). Omits empty groups from the output. Omits internal/infrastructure roles
 * (`notetaker`, `crow_handler`, etc.). `favorites` defaults to defaults-only (collaborator + rogue
 * are kind-favorited) when no prefs slice is supplied.
 *
 * `nowMs` is the current epoch-ms, used to compute the stuck-heartbeat (YELLOW health). It
 * defaults to `Date.now()` so callers that don't care about stuck detection need no change.
 * Pass an explicit `nowMs` in tests for determinism (the pure core never calls `Date.now()`
 * internally). `useCrowsView` injects the real clock as the single live-data injection point.
 */
export function selectCrowsView(
  state: RosterState,
  nowMs: number = Date.now(),
  favorites: FavoritesState = NO_FAVORITES,
): CrowsView {
  // Sort a copy (never mutate the readonly slice) before grouping so within-group order is stable.
  const sorted = [...state.rows].sort(byStatusThenId);

  // Whether a roster row is favorited — ORs the explicit star set with the kind-derived default
  // (collaborator + rogue), exactly like the chat-pane decision (item 9d). Derived from the row's
  // identity so the default matches `isDefaultFavorited`; rows with no identity are never favorited.
  const isRowFavorited = (row: RosterRow): boolean => {
    const identity = deriveAgentIdentity(row);
    return identity !== null && isFavorited(favorites, row.agentId, isDefaultFavorited(identity));
  };

  // Accumulate rows per group.
  const grouped = new Map<CrowGroup, RosterRow[]>(GROUP_ORDER.map((g) => [g, []]));
  for (const row of sorted) {
    const group = rowToGroup(row);
    if (group !== null) {
      grouped.get(group)?.push(row);
    }
  }

  // Build sections in spec order; omit empty groups. Within each group, favorited crows sort to the
  // top (stable re-partition over the status order — item 9d).
  const sections: CrowSection[] = [];
  for (const group of GROUP_ORDER) {
    const rows = grouped.get(group);
    if (rows !== undefined && rows.length > 0) {
      const favById = new Map(rows.map((r) => [r.agentId, isRowFavorited(r)]));
      const starredFirst = stableSortStarredFirst(
        rows,
        (r) => r.agentId,
        (id) => favById.get(id) ?? false,
      );
      sections.push({
        group,
        label: CROW_GROUP_LABEL[group],
        rows: starredFirst.map((r) => toRowView(r, nowMs, favById.get(r.agentId) ?? false)),
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
 * `Date.now()` is intentionally captured inside the memo factory — each re-render driven by a
 * slice change gets a fresh clock reading, which is the correct behaviour for stuck-heartbeat
 * detection. The pure `selectCrowsView` never calls `Date.now()` itself; this hook is the sole
 * clock injection point.
 *
 * Usage: `const view = useCrowsView(useAppStore((s) => s.roster));`
 */
export function useCrowsView(
  state: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): CrowsView {
  return useMemo(() => selectCrowsView(state, Date.now(), favorites), [state, favorites]);
}
