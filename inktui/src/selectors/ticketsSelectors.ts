/**
 * Tickets view-models — the selector (rule 2: ALL presentation lives here, never in the store or
 * the component). This is the chunk that proves the selector layer carries presentation weight for
 * richer multi-column panels.
 *
 * Copied from {@link ./notesSelectors.js} per the C6 copy recipe. Changes vs. notes:
 *  - Row type is {@link TicketRowView}: fully display-ready strings for a **2-row × 5-column**
 *    layout. EVERY formatted string lives here — no `.slice()`, join, or truncation anywhere in
 *    the component (rule 2).
 *  - Sort: by `lastUpdateAt` descending (most recently updated first, matching Python
 *    `_sort_schedule_rows`), then id for a stable tiebreak.
 *  - Column groups (per spec "Approach › Left panels › Tickets (4)"):
 *      5 column boxes, each 2 terminal lines tall (top/bottom):
 *        col 1: id (top)         / title (bottom)
 *        col 2: status (top)     / lastUpdate (bottom)
 *        col 3: deps (top)       / schedule (bottom)
 *        col 4: harness (top)    / model (bottom)
 *        col 5: plan (top)       / worktree (bottom)  ← CONTRACT GAP, both rendered as '—'
 *  - Deps cell: renders non-done dep ids from `pendingDepIds`; empty → `'ok'` sentinel.
 *  - Alternating-row color: a `rowParity` flag (0 | 1) lets the component apply a different
 *    `backgroundColor` every 2 rendered lines — the component reads it, not derives it.
 *  - Column widths are presentation constants here (not in the component — rule 2).
 *
 * Two layers, same as every selector:
 *  - **Pure transform** `selectTicketsView` — no React, unit-testable, callable from any frontend.
 *  - **`useMemo` hook** `useTicketsView` — component-facing wrapper that memoizes on slice identity.
 */

import { useMemo } from 'react';
import type { TicketRow, TicketsState } from '../store/tickets/ticketsSlice.js';

// === Column width constants (presentation — belong in the selector, not the store) ==============

/** Max width of the id cell (column 1 top). Ticket ids are short (e.g. "T-42") so 8 chars is generous. */
const ID_WIDTH = 8;
/** Max width of the title cell (column 1 bottom). Truncated with ellipsis. */
const TITLE_WIDTH = 24;
/** Max width of the last-update cell (column 2 bottom: "YYYY-MM-DD label"). */
const LAST_UPDATE_WIDTH = 20;
/** Max width of the deps cell (column 3 top: joined pending ids or sentinel). */
const DEPS_WIDTH = 24;
/** Max width of the schedule cell (column 3 bottom). */
const SCHEDULE_WIDTH = 12;
/** Max width of the harness cell (column 4 top). */
const HARNESS_WIDTH = 10;
/** Max width of the model cell (column 4 bottom: basename only). */
const MODEL_WIDTH = 18;
/** Max width of the plan cell (column 5 top). */
const PLAN_WIDTH = 16;
/** Max width of the worktree cell (column 5 bottom). */
const WORKTREE_WIDTH = 16;

/** Spaces a child ticket's title is indented under its parent (mirrors plans' `CHILD_INDENT`). */
const CHILD_INDENT = '    ';

// === Row view types ===========================================================================

/**
 * One ticket as the component paints it: fully display-ready column strings for the
 * **2-row × 5-column** layout, plus the `rowParity` flag for alternating background. ALL
 * formatting, truncation, and the deps cell calculation live here — the component does zero
 * formatting (rule 2).
 *
 * Layout — 5 `flexDirection="column"` boxes side by side (each 2 terminal lines):
 *   col 1: `idCell` (top)      / `titleCell` (bottom)
 *   col 2: `statusCell` (top)  / `lastUpdateCell` (bottom)
 *   col 3: `depsCell` (top)    / `scheduleCell` (bottom)
 *   col 4: `harnessCell` (top) / `modelCell` (bottom)
 *   col 5: `planCell` (top)    / `worktreeCell` (bottom)
 *
 * The component uses `rowParity` (0 or 1) to apply a subtle alternating background every 2 rendered
 * lines (every ticket occupies 2 terminal lines). The component also reads `depsSatisfied` to color
 * the deps cell without string-matching the sentinel (rule 2: color logic is presentation).
 */
export interface TicketRowView {
  readonly id: string;
  // === Column 1: id / title ====================================================================
  /** The ticket id, truncated to {@link ID_WIDTH}. */
  readonly idCell: string;
  /** The ticket title, truncated to {@link TITLE_WIDTH}. */
  readonly titleCell: string;
  // === Column 2: status / last-update ==========================================================
  /**
   * Status rendered as a single glyph (Goal A). The glyph encodes the precedence-ladder result —
   * 6 backend statuses + 3 DERIVED states (queued / scheduled / waiting-on-dependency). Replaces the
   * raw status string in the status cell. See {@link statusGlyphOf}.
   */
  readonly statusCell: string;
  /**
   * Semantic tone for the status glyph, derived purely from the ladder result (rule 2 — no
   * string-matching in the component). The component maps this to a theme color. See
   * {@link statusGlyphOf}.
   */
  readonly statusTone: StatusTone;
  /** `last_update_at` + `last_update_label`, truncated to {@link LAST_UPDATE_WIDTH}. */
  readonly lastUpdateCell: string;
  // === Column 3: deps / schedule ===============================================================
  /** Non-done dep ids joined by `', '`, or `'ok'` when all deps are satisfied. */
  readonly depsCell: string;
  /** `true` when all deps are satisfied (`pendingDepIds` was empty). Used for color without string-matching. */
  readonly depsSatisfied: boolean;
  /** Schedule display string, or `'—'` when unscheduled. */
  readonly scheduleCell: string;
  // === Column 4: harness / model ===============================================================
  /** Harness name (e.g. `'claude'`, `'codex'`), or `'—'` when absent. */
  readonly harnessCell: string;
  /** Model basename (provider prefix stripped), or `'—'` when absent. */
  readonly modelCell: string;
  // === Column 5: plan / worktree (CONTRACT GAP) ================================================
  /**
   * Plan cell — CONTRACT GAP: `plan` is ticket frontmatter, not on `ScheduleTicketRow` wire DTO.
   * Rendered as `'—'` until service B13 adds it to the row DTO.
   */
  readonly planCell: string;
  /**
   * Worktree cell — CONTRACT GAP: `worktree` is ticket frontmatter, not on `ScheduleTicketRow`
   * wire DTO. Rendered as `'—'` until service B13 adds it to the row DTO.
   */
  readonly worktreeCell: string;
  /**
   * `0` for even-indexed tickets, `1` for odd-indexed tickets (after sort). The component applies
   * a different `backgroundColor` based on this flag so every 2 terminal lines (= 1 ticket) get
   * alternating shading — "alternating color every 2 lines" per spec. Parity comes from the selector
   * (rule 2) so the component contains no index arithmetic.
   */
  readonly rowParity: 0 | 1;
  /** Tree depth: 0 top-level, 1 child (subticket). The indent is already baked into `titleCell`;
   * this is exposed for optional styling (mirrors `PlanRowView.depth`). */
  readonly depth: number;
}

/** The whole tickets list, render-ready. Parallel to {@link NotesView}. */
export interface TicketsView {
  readonly rows: readonly TicketRowView[];
  readonly status: TicketsState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

// === Pure formatting helpers (all presentation — not the store, not the component) =============

/** Truncate `text` to `width`, marking elision with an ellipsis. Silent truncation is a smell. */
function truncate(text: string, width: number): string {
  return text.length <= width ? text : `${text.slice(0, width - 1)}…`;
}

/** Strip `provider/model` prefix to the bare model name, mirroring Python `_compact_model`. */
function modelBasename(model: string | null): string {
  const raw = (model ?? '').trim();
  if (raw === '') {
    return '—';
  }
  const slash = raw.lastIndexOf('/');
  return slash === -1 ? raw : raw.slice(slash + 1);
}

/**
 * Deps cell: join non-done dep ids with `', '`, or `'ok'` when empty.
 * The `pendingDepIds` array already contains only non-done ids (service B5 — the DTO replaced
 * `deps_ok: bool` with this tuple). Empty = all deps satisfied.
 */
function formatDepsCell(pendingDepIds: readonly string[]): string {
  if (pendingDepIds.length === 0) {
    return 'ok';
  }
  return truncate(pendingDepIds.join(', '), DEPS_WIDTH);
}

/** Last-update cell: id + label, truncated. The id is an ISO string; display it as-is. */
function formatLastUpdateCell(lastUpdateAt: string, lastUpdateLabel: string): string {
  // Show compact form: "YYYY-MM-DD label" or "HH:MM label" (the Python cell does more logic;
  // for the Ink view we keep it simple and use ISO slice + label).
  const compact = lastUpdateAt.slice(0, 10); // YYYY-MM-DD
  return truncate(`${compact} ${lastUpdateLabel}`, LAST_UPDATE_WIDTH);
}

/**
 * Semantic tone for a ticket status glyph, consumed by the panel to pick a theme color (Goal A).
 * `blocked` is its OWN tone — deliberately NOT lumped with `error` — so the panel can paint a
 * blocked ticket distinctly from a failed one.
 */
export type StatusTone = 'error' | 'success' | 'warning' | 'blocked' | 'neutral';

/** One glyph + its semantic tone — the result of the status precedence ladder (Goal A). */
export interface StatusGlyph {
  readonly glyph: string;
  readonly tone: StatusTone;
}

/**
 * The status precedence ladder (Goal A — first match wins). 6 of these glyphs map to backend
 * statuses; 3 are DERIVED from the row's runtime fields:
 *
 *   ◌ draft      ○ ready    ◕ queued     ◷ scheduled   ◍ waiting-on-dependency
 *   ● running    ⊘ blocked  ✓ done       ✗ failed
 *
 * Ladder:
 *   1. `failed`              → ✗ (error tone)
 *   2. `done`               → ✓ (success tone)
 *   3. `blocked`            → ⊘ (its OWN `blocked` tone, NOT error)
 *   4. `in_progress`        → ● running (warning tone)
 *   5. `draft` / `planned`  → ◌ (neutral; `planned` shares draft's neutral pre-actionable glyph)
 *   6. `ready`:
 *        a. unmet deps (`pendingDepIds` non-empty) → ◍ waiting-on-dependency
 *        b. else `scheduleAt` is in the FUTURE     → ◷ scheduled
 *        c. else (eligible: deps ok, not future)   → ◕ queued
 *        d. fallback                               → ○ ready
 *   7. `archived` (or anything unknown) → ○ ready glyph, neutral (archived renders in its own section).
 *
 * Pure (rule 2 — all status semantics live here, never string-matched in the component). The
 * `now`/`scheduleAt` future check is the only time-dependent branch; callers pass `now` for
 * determinism in tests.
 */
export function statusGlyphOf(row: TicketRow, now: number = Date.now()): StatusGlyph {
  switch (row.status.toLowerCase()) {
    case 'failed':
      return { glyph: '✗', tone: 'error' };
    case 'done':
      return { glyph: '✓', tone: 'success' };
    case 'blocked':
      return { glyph: '⊘', tone: 'blocked' };
    case 'in_progress':
      return { glyph: '●', tone: 'warning' };
    case 'draft':
    case 'planned':
      // `planned` shares draft's neutral pre-actionable glyph (same ◌ — both are pre-actionable).
      return { glyph: '◌', tone: 'neutral' };
    case 'ready': {
      if (row.pendingDepIds.length > 0) {
        return { glyph: '◍', tone: 'neutral' }; // a: waiting-on-dependency
      }
      if (row.scheduleAt != null && isFuture(row.scheduleAt, now)) {
        return { glyph: '◷', tone: 'neutral' }; // b: scheduled (future)
      }
      return { glyph: '◕', tone: 'neutral' }; // c: queued (eligible)
    }
    default:
      // d/7: plain ready fallback + archived/unknown — the neutral default glyph.
      return { glyph: '○', tone: 'neutral' };
  }
}

/**
 * Whether an ISO-8601 `scheduleAt` string parses to a time strictly AFTER `now`. A value that
 * doesn't parse to a real date (e.g. a pre-formatted label) is treated as NOT future, so it falls
 * through to the queued glyph rather than mis-claiming "scheduled".
 */
function isFuture(scheduleAt: string, now: number): boolean {
  const t = Date.parse(scheduleAt);
  return !Number.isNaN(t) && t > now;
}

/**
 * Project one domain row into its display-ready presentation tuple. `index` drives `rowParity`,
 * `depth` drives the subticket indent (title pre-indented here, rule 2), `now` feeds the
 * scheduled-vs-future check in the status ladder.
 */
function toTicketRowView(row: TicketRow, index: number, depth: number, now: number): TicketRowView {
  const satisfied = row.pendingDepIds.length === 0;
  const status = statusGlyphOf(row, now);
  return {
    id: row.id,
    // col 1 — title pre-indented for a subticket (mirrors plans' `toRowView`).
    idCell: truncate(row.id, ID_WIDTH),
    titleCell: depth > 0 ? `${CHILD_INDENT}${truncate(row.title, TITLE_WIDTH)}` : truncate(row.title, TITLE_WIDTH),
    // col 2 — status rendered as a glyph (Goal A).
    statusCell: status.glyph,
    statusTone: status.tone,
    lastUpdateCell: formatLastUpdateCell(row.lastUpdateAt, row.lastUpdateLabel),
    // col 3
    depsCell: formatDepsCell(row.pendingDepIds),
    depsSatisfied: satisfied,
    scheduleCell: row.scheduleAt != null ? truncate(row.scheduleAt, SCHEDULE_WIDTH) : '—',
    // col 4
    harnessCell: truncate(row.harness ?? '—', HARNESS_WIDTH),
    modelCell: truncate(modelBasename(row.model), MODEL_WIDTH),
    // col 5 — CONTRACT GAP: plan/worktree not on wire DTO; see ticketsSlice.ts note
    planCell: truncate('—', PLAN_WIDTH),
    worktreeCell: truncate('—', WORKTREE_WIDTH),
    rowParity: (index % 2 === 0 ? 0 : 1) as 0 | 1,
    depth,
  };
}

/** Sort: by `lastUpdateAt` descending (most recent first), then id for a stable tiebreak.
 * Mirrors Python `_sort_schedule_rows` (sort by id then by last_update_at desc). */
function byLastUpdateDescThenId(a: TicketRow, b: TicketRow): number {
  const cmp = b.lastUpdateAt.localeCompare(a.lastUpdateAt);
  return cmp !== 0 ? cmp : a.id.localeCompare(b.id);
}

/**
 * The pure view-model transform (Goal B — subticket tree). Mirrors `selectPlansView`'s tree build:
 * partition rows into top-level parents and children-of-known-parents (a child whose `parent` is
 * missing from the list is treated as top-level — never drop a row), sort within each band by
 * `lastUpdateAt` desc, then flatten parent → its children (children rendered indented under the
 * parent). The status glyph is computed per row at any depth. Same input → same output; no React,
 * no store, no bus.
 *
 * `now` feeds the scheduled-vs-future glyph branch; it defaults to wall-clock and is injectable for
 * deterministic tests.
 */
export function selectTicketsView(state: TicketsState, now: number = Date.now()): TicketsView {
  const all = state.rows;
  const byId = new Map<string, TicketRow>(all.map((r) => [r.id, r]));

  // 1. Partition into top-level tickets and children-of-known-parents (mirror plans step 1).
  const childrenOf = new Map<string, TicketRow[]>();
  const tops: TicketRow[] = [];
  for (const r of all) {
    if (r.parent !== null && byId.has(r.parent)) {
      const bucket = childrenOf.get(r.parent);
      if (bucket === undefined) {
        childrenOf.set(r.parent, [r]);
      } else {
        bucket.push(r);
      }
    } else {
      tops.push(r);
    }
  }

  // 2. Order top-level tickets, then flatten each with its (ordered) children indented under it.
  //    Children keep the same last-update-desc order as the top level.
  tops.sort(byLastUpdateDescThenId);
  const ordered: { row: TicketRow; depth: number }[] = [];
  for (const parent of tops) {
    ordered.push({ row: parent, depth: 0 });
    const kids = (childrenOf.get(parent.id) ?? []).slice().sort(byLastUpdateDescThenId);
    for (const kid of kids) {
      ordered.push({ row: kid, depth: 1 });
    }
  }

  const rows = ordered.map(({ row, depth }, index) => toTicketRowView(row, index, depth, now));
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectTicketsView} on the slice identity. A component does:
 *   `const view = useTicketsView(useAppStore((s) => s.tickets));`
 */
export function useTicketsView(state: TicketsState): TicketsView {
  return useMemo(() => selectTicketsView(state), [state]);
}
