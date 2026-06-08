/**
 * Ticket-detail slice — holds the body (and display-only frontmatter) of the currently open ticket.
 *
 * Copied from {@link ../tickets/ticketsSlice.js} per the C3 copy recipe. Key differences:
 *  - This is NOT an event-invalidated slice (no `INVALIDATING_ENTITY` — the detail is loaded on
 *    demand when the user presses enter on a ticket, not on every bus snapshot). The editor
 *    controls loading/saving explicitly via its actions.
 *  - The editable document is the ticket **body** (a markdown string). Frontmatter fields
 *    (`title`, `deps`, `harness`, `model`, `worktree`) are display-only context delivered here
 *    for rendering the editor header — they are NOT part of the editable body, and runtime state
 *    (`status`, `schedule_at`, `attempts`) is DB-only and lives only in the tickets list row DTO.
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
 * sends the duration. Both are modeled-not-live (FakeBusClient only) until service B13.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/**
 * Frontmatter fields carried in the detail reply for display-only context in the editor header.
 * These are NOT editable here — the editor shows them as read-only context (title, deps, harness,
 * model, worktree). Matches the ticket frontmatter from `murder/work/tickets/`.
 */
export interface TicketFrontmatter {
  readonly title: string;
  /** Dependency ticket ids (comma-separated string as it appears in the .md frontmatter). */
  readonly deps: string;
  readonly harness: string | null;
  readonly model: string | null;
  readonly worktree: string | null;
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
 * Note: no `*_INVALIDATING_ENTITY` — this slice is demand-loaded, not snapshot-driven.
 */
export const createTicketDetailSlice: StateCreator<
  AppStore,
  [],
  [],
  { ticketDetail: TicketDetailState }
> = () => ({
  ticketDetail: initialTicketDetailState,
});
