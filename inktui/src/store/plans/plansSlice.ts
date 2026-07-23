/**
 * Plans slice — domain state for the plans list (panel 1).
 *
 * Copied from {@link ../notes/notesSlice.js} per the copy recipe, with ONE extra field: `parent`.
 * Plans form a tree (a child plan names its parent), which drives parent/child indentation in the
 * selector (rule 2 — the slice stays flat, the selector computes the tree + indent + ordering). The
 * slice holds raw, wire-faithful domain data only; the `parent` linkage is a stored field, never a
 * pre-computed indent or pre-nested structure.
 *
 * The shared `{ rows, status, error }` mechanics come from the generic {@link ListState} +
 * {@link createListSlice} factory — this file is a thin shell over it, exactly like notes/reports.
 * Only the row type (with `parent`), the slice key (`plans`), and the invalidating entity differ.
 */

import { createListSlice, initialListState, type ListState } from '../listSlice.js';

/**
 * One plan as the plans slice cares about it — a faithful, presentation-free projection of the
 * service's plan-row DTO. `updatedAt` is the ISO-8601 string from the wire; `parent` is the
 * filename/name of this plan's parent plan, or `null` for a top-level plan. Mirrors the backend
 * `parent` field on plan rows (bus contract › "Payload / DTO shapes": "`parent` on plan rows
 * drives parent/child indentation (service supplies; Ink indents)").
 *
 * Presentation (sort order, indentation, recency-bubbling) lives in the selector (rule 2), never
 * here — so the slice stays reusable by a future React-DOM client.
 */
export interface PlanRow {
  readonly name: string;
  readonly charCount: number;
  /** ISO-8601 string as the wire delivers it. The selector formats it for display. */
  readonly updatedAt: string;
  /** The `name` of this plan's parent plan, or `null` for a top-level plan. Drives the tree. */
  readonly parent: string | null;
}

/**
 * The plans slice's state — the shared {@link ListState} shape specialized to {@link PlanRow}.
 * Selectors read `PlansState['status']`, so the `'idle' | 'loading' | 'ready' | 'error'` union is
 * part of the contract.
 */
export type PlansState = ListState<PlanRow>;

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialPlansState: PlansState = initialListState<PlanRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `plans` key, built from the
 * shared {@link createListSlice}. Contributes only the `plans` key; `../store.ts` composes it.
 */
export const createPlansSlice = createListSlice('plans', initialPlansState);
