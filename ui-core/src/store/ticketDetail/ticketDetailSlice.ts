/**
 * Ticket-detail slice — holds the body (and display-only frontmatter) of the currently open ticket.
 *
 * Copied from {@link ../tickets/ticketsSlice.js} per the C3 copy recipe. Key differences:
 *  - This is a demand-loaded slice: the detail is loaded on
 *    demand when the user presses enter on a ticket, not on every bus snapshot). The editor
 *    controls loading/saving explicitly via its actions.
 *  - The editable document is the ticket **body** (a markdown string). Display-only context
 *    (`title`, `status`, `deps`, `harness`, `model`, `worktree`, `scheduleAt`) is delivered here
 *    for rendering the editor header — none of it is part of the editable body. The body carries
 *    the `# Checklist` `[ ]`/`[x]` lines (C8 line 167); the schedule is changed via the free-form
 *    duration input + `ticket.schedule`, never by editing `scheduleAt`.
 *  - `editedBody` is the *in-progress* editor buffer (may differ from `savedBody` if the user
 *    has made edits); `savedBody` is the last-persisted version from the service.
 *  - `scheduleInput` is the free-form duration string the user types (`1d4h3m`, `34m`); it is
 *    validated client-side and sent via the `ticket.schedule` RPC. It is separate from the body
 *    save — the backend runs `parse_duration()` authoritatively (the client regex is display-only).
 *
 * Shape note: `ticketId: null` when no ticket is open. Non-null = a ticket is open (possibly
 * loading). The editor mode reads this to decide whether to paint.
 *
 * To use this slice: call `actions.ticketDetail.open(id)` to load; the editor mode enters itself
 * and reads the slice. `actions.ticketDetail.saveBody()` writes back; `actions.ticketDetail.schedule()`
 * sends the duration.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/**
 * Display-only context carried in the detail reply, shown read-only in the editor header. Combines
 * the ticket frontmatter (`title, deps, harness, model, worktree`) with the runtime state the
 * service now delivers alongside the doc (`status`, `scheduleAt`). None of these are editable here —
 * the body (and its `# Checklist` lines) is the only editable surface; the schedule is changed via
 * the free-form duration input + `ticket.schedule`, not by editing `scheduleAt`.
 */
export interface TicketFrontmatter {
  readonly title: string;
  /** Ticket lifecycle status (e.g. `"planned"`, `"in_progress"`). Display-only. */
  readonly status: string;
  /** Dependency ticket ids, joined to a comma-separated string for display. */
  readonly deps: string;
  readonly harness: string | null;
  readonly model: string | null;
  readonly worktree: string | null;
  /** ISO timestamp the ticket is currently scheduled for, or `null`. Display-only. */
  readonly scheduleAt: string | null;
}

/**
 * The ticket-detail slice state. All fields readonly — ref-swapped wholesale on change.
 * `ticketId: null` = closed; non-null = a ticket is open (loading, editing, or errored).
 */
export interface TicketDetailState {
  /** The ticket currently open in the editor, or `null` when closed. */
  readonly ticketId: string | null;
  /** Frontmatter for display-only context in the editor header. `null` while loading or closed. */
  readonly frontmatter: TicketFrontmatter | null;
  /** The last-persisted body from the service. `null` while loading or closed. */
  readonly savedBody: string | null;
  /**
   * The in-progress editor buffer. Starts as `savedBody` when the ticket loads; the editor
   * mutates this as the user types. Checked against `savedBody` to decide whether to show
   * an unsaved-changes marker.
   */
  readonly editedBody: string | null;
  /**
   * The free-form schedule input (`1d4h3m`, `34m`). Separate from the body — sent via
   * `ticket.schedule`, not `ticket.save_body`. Empty string = no schedule input pending.
   */
  readonly scheduleInput: string;
  /** Whether the `ticket.schedule` validation passes client-side (for inline feedback). */
  readonly scheduleValid: boolean;
  readonly status: 'idle' | 'loading' | 'saving' | 'ready' | 'error';
  readonly error: string | null;
}

/** Initial (closed) state. */
export const initialTicketDetailState: TicketDetailState = {
  ticketId: null,
  frontmatter: null,
  savedBody: null,
  editedBody: null,
  scheduleInput: '',
  scheduleValid: false,
  status: 'idle',
  error: null,
};

/**
 * Slice factory. Contributes only the `ticketDetail` key; `../store.ts` composes it.
 * This slice is demand-loaded, not projection-snapshot-driven.
 */
export const createTicketDetailSlice: StateCreator<
  AppStore,
  [],
  [],
  { ticketDetail: TicketDetailState }
> = () => ({
  ticketDetail: initialTicketDetailState,
});
