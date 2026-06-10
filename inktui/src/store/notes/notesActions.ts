/**
 * Notes actions — the *only* code that calls the bus for notes data (rule 3).
 *
 * Copied from {@link ../roster/rosterActions.js} per the copy recipe. Changes vs. the roster:
 *  - RPC is `state.notes_snapshot` (modeled per bus contract naming — NOT yet on the live bus; B13).
 *  - Reply shape mirrors Python `NotesSnapshot` (notes[] with name/char_count/updated_at).
 *  - Projection is `toNoteRow` (name → name, char_count, updated_at as strings).
 *  - Passes the `notes` slice key to `createRefreshAction`.
 *  - `declare module` augments `RpcMethods` with `'state.notes_snapshot'` (distinct from the roster's
 *    `'state.crow_snapshot'` — each slice owns its own key; never redeclare an existing one).
 *
 * The loading→ready/error + ref-swap-only-this-key mechanics come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js`.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { NoteRow } from './notesSlice.js';

/**
 * Declares the notes read RPC via declaration merging rather than editing the frozen C1 bus files.
 * `state.notes_snapshot` is the bus-contract name (`domain.verb`, mirrors Python
 * `RuntimeClient.get_notes_snapshot`). NOT yet on the live bus — modeled here per the contract's
 * "view → service = RPC methods" rule; confirm the name/shape when service B13 lands.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full notes list. Re-pulled on each `note`-entity `state.snapshot`. */
    'state.notes_snapshot': { params: Record<string, never>; result: NotesSnapshotReply };
  }
}

/**
 * The `state.notes_snapshot` reply, mirroring the service's `NotesSnapshot` DTO from
 * `murder/app/service/client_api.py`. Only the fields the notes slice projects are typed.
 */
export interface NotesSnapshotReply {
  notes: readonly NoteDto[];
  invalidation_key: string;
}

/** One note as it crosses the wire (Python `NoteSummary`). Presentation-free. */
export interface NoteDto {
  name: string;
  char_count: number;
  /** ISO-8601 datetime string (Python `datetime.isoformat()`). */
  updated_at: string;
}

/** Project one wire note into the slice's row. Pure: the single place the DTO→domain mapping
 * lives. No formatting — that is the selector's job (rule 2). */
function toNoteRow(dto: NoteDto): NoteRow {
  return {
    name: dto.name,
    charCount: dto.char_count,
    updatedAt: dto.updated_at,
  };
}

/**
 * The notes actions, bound to one `BusClient` + store handle. Returned to `../store.ts`, which
 * hangs them off the store so components dispatch `store.getState().actions.notes.refresh()`.
 */
export interface NotesActions {
  /**
   * Re-pull the notes list and ref-swap *only* the `notes` slice. The sole bus caller for note
   * data. Idempotent; concurrent calls are last-write (latest reply wins). Rejections land in
   * `notes.error` — never thrown past the action.
   */
  refresh(): Promise<void>;
}

export function createNotesActions(bus: BusClient, store: StoreApi<AppStore>): NotesActions {
  return createRefreshAction(bus, store, {
    key: 'notes',
    method: 'state.notes_snapshot',
    project: (reply) => reply.notes.map(toNoteRow),
  });
}
