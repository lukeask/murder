/**
 * Notes slice — domain state for the notes list (panel 2).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the C3 copy recipe. Only these change vs.
 * the roster: the row shape mirrors {@link NoteSummary} from `murder/app/service/client_api.py`
 * (name + char_count + updated_at), and the invalidating entity is `'note'` (not `'agent'`).
 * Presentation (sort order, updated_at formatting) stays out of this file — it belongs in the
 * selector (rule 2). The slice holds raw, wire-faithful domain data only.
 *
 * Copy this file to add slice X: rename state/row types, swap the row fields for X's DTO,
 * change NOTES_INVALIDATING_ENTITY to X's Entity key, rename the slice key + initial const.
 */

import type { StateCreator } from 'zustand';
import type { Entity } from '../../bus/protocol.js';
import type { AppStore } from '../store.js';

/**
 * One note as the notes slice cares about it — a faithful, presentation-free projection of the
 * service's NoteSummary DTO. `updatedAt` is the ISO-8601 string from the wire (Python
 * `datetime.isoformat()`); formatting it for display is the selector's job (rule 2).
 */
export interface NoteRow {
  readonly name: string;
  readonly charCount: number;
  /** ISO-8601 string as the wire delivers it. The selector formats it for display. */
  readonly updatedAt: string;
}

/**
 * The notes slice's state. Same shape as {@link RosterState}: `rows` is domain data, `status`
 * makes the load lifecycle explicit so a component can distinguish "not fetched" from "fetched,
 * empty". Every field is readonly — the slice is ref-swapped wholesale, never mutated in place.
 */
export interface NotesState {
  readonly rows: readonly NoteRow[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. The service emits
 * `'note'`-keyed change events when the notes list changes; the store re-pulls only the notes
 * slice (see `../store.ts`).
 */
export const NOTES_INVALIDATING_ENTITY: Entity = 'note';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialNotesState: NotesState = {
  rows: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory in Zustand's standard `StateCreator` shape, scoped to the combined
 * {@link AppStore}. Contributes only the `notes` key; `../store.ts` composes it with siblings.
 */
export const createNotesSlice: StateCreator<AppStore, [], [], { notes: NotesState }> = () => ({
  notes: initialNotesState,
});
