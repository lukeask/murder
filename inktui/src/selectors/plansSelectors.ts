/**
 * Plans view-model — the selector (rule 2: ALL presentation here, never in the store or component).
 *
 * This selector is the hardest in the app: it reconciles THREE orderings at once, in a deliberate
 * precedence (decided here, documented so a later panel copies the resolution rather than re-inventing):
 *
 *   1. **Parent tree + 4-space indent (spec › Parent plans).** Children are listed directly under
 *      their parent, their `name` indented 4 spaces. The slice is flat (a `parent` field per row);
 *      this selector builds the parent→children grouping. One level of nesting is modeled (the spec
 *      describes parent/child, not arbitrary depth); a child naming a missing/unknown parent is
 *      treated as top-level (defensive — the tree never drops a row).
 *
 *   2. **Child-recency bubbles the parent's position (spec › Parent plans).** "A child's more-recent
 *      update counts for the parent's ordering position." So a parent *group* is ordered by its
 *      EFFECTIVE recency = max(parent.updatedAt, every child.updatedAt). Within a group, children
 *      are ordered by their own recency. This is why ordering is computed over groups, not flat rows.
 *
 *   3. **Starred to the top (spec › Starring).** A favorited plan floats to the top. Applied at the
 *      GROUP level on top of the recency order: a group whose PARENT is starred sorts before
 *      non-starred groups (stable within each block, preserving the recency order from step 2). A
 *      starred *child* floats within its parent's child list (starred children first, then by
 *      recency) — but a child can't leave its parent (it would lose the indent relationship), so
 *      child-starring reorders only within the subtree. The common case (starring a top-level plan)
 *      floats the whole group, which is the visible "starred at top" behaviour.
 *
 * Precedence, stated once: **starred-block partition (top) → effective-recency order (within block)
 * → tree flatten (parent, then its children) → child star/recency order (within subtree).** Each
 * step is a stable transform layered on the previous, so no step fights another.
 *
 * Two layers (same as every selector):
 *  - Pure transform `selectPlansView` — no React, unit-testable, callable from any frontend.
 *  - `useMemo` hook `usePlansView` — memoised on the (plans, favorites) slice identities.
 */

import { useMemo } from 'react';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { PlanRow, PlansState } from '../store/plans/plansSlice.js';
import { isInFavoriteSet } from './favoritesSelectors.js';

/** Spaces a child's name is indented under its parent (spec: "name indented 4 spaces"). */
const CHILD_INDENT = '    ';

/**
 * One plan row as the component paints it: display-ready strings plus the flags the component needs
 * to render the star marker and pick a focusable cursor row. Indentation is baked into `name` (rule
 * 2 — the component never computes indent); `depth` is exposed for styling if a panel wants it.
 */
export interface PlanRowView {
  /** The plan's stable id (its filename) — the favorite id and the row key. */
  readonly id: string;
  /** Display name, already indented 4 spaces if this is a child (rule 2). */
  readonly name: string;
  /** Char count formatted as a compact display string. */
  readonly charCount: string;
  /** `updated_at` formatted `YYYY-MM-DD HH:MM`. */
  readonly updatedAt: string;
  /** Tree depth: 0 top-level, 1 child. The indent is already in `name`; this is for optional styling. */
  readonly depth: number;
  /** Whether this plan is starred (in the explicit favorite set). The component renders a marker. */
  readonly starred: boolean;
}

/** The whole plans list, render-ready and in final display order. Parallel to {@link NotesView}. */
export interface PlansView {
  readonly rows: readonly PlanRowView[];
  readonly status: PlansState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** Format an ISO-8601 datetime to `YYYY-MM-DD HH:MM` (mirrors notes/reports formatting). */
function formatUpdatedAt(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ');
}

/** Format a character count as a compact, human-readable display string. */
function formatCharCount(n: number): string {
  return `${n.toLocaleString()} chars`;
}

/** A parent and its (recency-ordered) children — the unit ordering operates over. */
interface PlanGroup {
  readonly parent: PlanRow;
  readonly children: readonly PlanRow[];
  /** Effective recency = max(parent, children) ISO string. Children bubble the parent's position. */
  readonly effectiveUpdatedAt: string;
  /** Whether the PARENT is starred — floats the whole group to the top block. */
  readonly parentStarred: boolean;
}

/** Most-recent ISO string of a parent + its children (ISO strings sort lexicographically by date). */
function maxUpdatedAt(parent: PlanRow, children: readonly PlanRow[]): string {
  let max = parent.updatedAt;
  for (const c of children) {
    if (c.updatedAt > max) {
      max = c.updatedAt;
    }
  }
  return max;
}

/** Descending recency, tiebreak ascending name — the within-group child order and the group order. */
function byRecencyThenName(a: PlanRow, b: PlanRow): number {
  const cmp = b.updatedAt.localeCompare(a.updatedAt);
  return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
}

/**
 * The pure view-model transform. Builds the parent tree, orders groups by effective recency with
 * starred groups floated to the top, orders children within each subtree (starred children first,
 * then recency), and flattens to the final indented row list. Same input → same output; no React,
 * no store, no bus.
 */
export function selectPlansView(state: PlansState, favorites: FavoritesState): PlansView {
  const all = state.rows;
  const byName = new Map<string, PlanRow>(all.map((p) => [p.name, p]));
  const starred = (p: PlanRow): boolean => isInFavoriteSet(favorites, p.name);

  // 1. Partition into top-level parents and children-of-known-parents. A child whose parent is
  //    missing from the list is treated as top-level (defensive — never drop a row).
  const childrenOf = new Map<string, PlanRow[]>();
  const tops: PlanRow[] = [];
  for (const p of all) {
    if (p.parent !== null && byName.has(p.parent)) {
      const bucket = childrenOf.get(p.parent);
      if (bucket === undefined) {
        childrenOf.set(p.parent, [p]);
      } else {
        bucket.push(p);
      }
    } else {
      tops.push(p);
    }
  }

  // 2. Build groups. Within a group, children are starred-first then recency (a starred child floats
  //    within its parent's subtree, but cannot leave the parent — see the module doc).
  const groups: PlanGroup[] = tops.map((parent) => {
    const kids = (childrenOf.get(parent.name) ?? []).slice();
    kids.sort(byRecencyThenName);
    kids.sort((a, b) => Number(starred(b)) - Number(starred(a))); // stable: starred kids first
    return {
      parent,
      children: kids,
      effectiveUpdatedAt: maxUpdatedAt(parent, kids),
      parentStarred: starred(parent),
    };
  });

  // 3. Order groups: starred-parent groups first (the visible "starred at top"), then by effective
  //    recency. Two stable sorts layered: recency first, then float the starred block.
  groups.sort((a, b) => {
    const cmp = b.effectiveUpdatedAt.localeCompare(a.effectiveUpdatedAt);
    return cmp !== 0 ? cmp : a.parent.name.localeCompare(b.parent.name);
  });
  groups.sort((a, b) => Number(b.parentStarred) - Number(a.parentStarred)); // stable: starred groups first

  // 4. Flatten: each group emits its parent (depth 0) then its children (depth 1, indented).
  const rows: PlanRowView[] = [];
  for (const group of groups) {
    rows.push(toRowView(group.parent, 0, starred(group.parent)));
    for (const child of group.children) {
      rows.push(toRowView(child, 1, starred(child)));
    }
  }

  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/** Project one row into its presentation tuple at the given depth. */
function toRowView(row: PlanRow, depth: number, isStarred: boolean): PlanRowView {
  return {
    id: row.name,
    name: depth > 0 ? `${CHILD_INDENT}${row.name}` : row.name,
    charCount: formatCharCount(row.charCount),
    updatedAt: formatUpdatedAt(row.updatedAt),
    depth,
    starred: isStarred,
  };
}

/**
 * Component-facing hook: memoises {@link selectPlansView} on the (plans, favorites) slice identities.
 * Re-runs only when either slice ref-changes — so re-grouping happens on a real plans change or a
 * star toggle, not on unrelated re-renders.
 */
export function usePlansView(state: PlansState, favorites: FavoritesState): PlansView {
  return useMemo(() => selectPlansView(state, favorites), [state, favorites]);
}
