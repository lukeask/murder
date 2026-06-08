/**
 * Notes slice — domain state for the notes list (panel 2).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the copy recipe. Only these change vs. the
 * roster: the row shape mirrors {@link NoteSummary} from `murder/app/service/client_api.py`
 * (name + char_count + updated_at), and the invalidating entity is `'note'` (not `'agent'`).
 * Presentation (sort order, updated_at formatting) stays out of this file — it belongs in the
 * selector (rule 2). The slice holds raw, wire-faithful domain data only.
 *
 * The shared `{ rows, status, error }` mechanics come from the generic {@link ListState} +
 * {@link createListSlice} factory in `../listSlice.js` — this file is a thin shell over it.
 *
 * Copy this file to add slice X: rename the row type + its fields for X's DTO, change
 * NOTES_INVALIDATING_ENTITY to X's Entity key, and pass X's key to `createListSlice`.
 */

import type { Entity } from '../../bus/protocol.js';
import { createListSlice, initialListState, type ListState } from '../listSlice.js';

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
 * The notes slice's state — the shared {@link ListState} shape specialized to {@link NoteRow}.
 * `rows` is domain data, `status` makes the load lifecycle explicit. Selectors read
 * `NotesState['status']`, so the `'idle' | 'loading' | 'ready' | 'error'` union is part of the
 * contract.
 */
export type NotesState = ListState<NoteRow>;

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. The service emits
 * `'note'`-keyed change events when the notes list changes; the store re-pulls only the notes
 * slice (see `../store.ts`).
 */
export const NOTES_INVALIDATING_ENTITY: Entity = 'note';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialNotesState: NotesState = initialListState<NoteRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `notes` key, built from the
 * shared {@link createListSlice}. Contributes only the `notes` key; `../store.ts` composes it.
 */
export const createNotesSlice = createListSlice('notes', initialNotesState);
