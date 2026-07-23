/**
 * Ticket-detail actions — the *only* code that calls the bus for ticket detail data (rule 3).
 *
 * Three live RPCs:
 *  1. `state.ticket_detail {ticket_id}` → body string + frontmatter for display.
 *  2. `ticket.save_body {ticket_id, body}` → `{ok}` — service syncs the markdown body to DB.
 *  3. `ticket.schedule {ticket_id, duration}` → `{ok}` — service runs `parse_duration()` and
 *     updates `schedule_at`. The raw string (e.g. `"1d4h3m"`) is sent as-is; the backend is
 *     authoritative. Client-side regex validation (`DURATION_RE`) provides inline UX only.
 *
 * The `state.ticket_detail` response (Python `TicketDetailSnapshot`, unwrapped from `{ok, value}`)
 * carries:
 *  - `body: string` — the full markdown body (includes `# Checklist` section with `[ ]`/`[x]`
 *    lines). This is the editable document; the editor toggles checklist items as normal body edits.
 *  - Frontmatter fields for display-only context in the editor header (`title`, `deps`, `harness`,
 *    `model`, `worktree`). These are NOT editable here — the editor shows them read-only.
 *  - Runtime state `status` (ticket lifecycle) and `schedule_at` (current scheduled time), now
 *    delivered alongside the doc and shown read-only in the header / schedule row.
 *  - `checklist` (structured `{text, done}[]`) — carried for contract fidelity, NOT rendered; the
 *    body is the single checklist source (C8 line 167).
 *
 * Duration grammar (mirrors `murder/work/duration.py`):
 *  Accepted: `1d4h3m`, `1h1m`, `34m`, `1h`, `2d`. Units d/h/m in order, each at most once.
 *  Rejected: empty, bare number (no unit), unknown units, negative, out-of-order, duplicate.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import type { TicketDetailState, TicketFrontmatter } from './ticketDetailSlice.js';

// ── RPC declarations ────────────────────────────────────────────────────────────────────────────

/**
 * Augments `RpcMethods` with the three live ticket-detail RPCs. Reads are registered by
 * `murder/app/service/handlers/state.py`; writes are registered by
 * `murder/app/service/handlers/ticket.py`.
 */


// ── Wire DTO ────────────────────────────────────────────────────────────────────────────────────

/**
 * One structured checklist item from the detail reply (`{text, done}`). Mirrors the Python
 * `ChecklistItem` dataclass (`murder/app/protocol/read_models.py`). NOTE: the editor does NOT render
 * from this — per newui-inktui C8 (line 167) the checklist **rides inside `body`** under the
 * `# Checklist` heading as `[ ]`/`[x]` lines, and the editor toggles those body lines directly
 * (`toggleChecklist` in `TicketEditorMode.tsx`). This field is carried for contract fidelity with
 * the wire DTO (and for any non-editing consumer that wants the parsed form), not consumed for
 * rendering — picking ONE source of truth (the body) avoids a divergent second checklist view.
 */
export interface ChecklistItem {
  text: string;
  done: boolean;
}

/**
 * The `state.ticket_detail` reply — the wire shape of the Python `TicketDetailSnapshot`
 * (`murder/app/protocol/read_models.py`), unwrapped from the `{ok, value}` read envelope by the
 * shared bus util before it reaches here. Field-by-field with the dataclass:
 *  - `id`, `title`, `body` — strings (`body` is frontmatter-stripped and INCLUDES the `# Checklist`
 *    `[ ]`/`[x]` section per C8 line 167; it is the editable document).
 *  - `status` — the ticket lifecycle status (`TicketStatus` enum value, e.g. `"planned"`).
 *    Display-only in the editor header; distinct from the slice's load `status`.
 *  - `deps` — `string[]` (pending dependency ids). Joined to a comma string for the header.
 *  - `harness`/`model`/`worktree` — nullable display-only header fields.
 *  - `schedule_at` — nullable ISO timestamp the ticket is currently scheduled for; backs the
 *    schedule row display. NOT the same as the free-form duration the user types (`scheduleInput`).
 *  - `checklist` — structured `{text, done}[]`; carried for contract fidelity, NOT rendered (body
 *    is the single source — see {@link ChecklistItem}).
 * The reply also carries `as_of`/`invalidation_key` metadata (not declared here).
 */
export interface TicketDetailReply {
  id: string;
  title: string;
  status: string;
  /** Pending dependency ids. Python sends an array; joined to a string for the header. */
  deps: string[];
  harness?: string | null;
  model?: string | null;
  worktree?: string | null;
  /** Nullable ISO timestamp the ticket is currently scheduled for (display-only). */
  schedule_at?: string | null;
  /** Full markdown body including the `# Checklist` section. The editable document. */
  body: string;
  /** Structured checklist mirror — carried for contract fidelity, NOT rendered (body is the source). */
  checklist?: ChecklistItem[];
}

// ── Duration validation (client-side, display-only — mirrors murder/work/duration.py) ──────────

/**
 * Client-side duration regex for inline validation feedback. Mirrors the Python
 * `_DURATION_RE` in `murder/work/duration.py` (anchored, d/h/m order, each optional).
 * The backend is authoritative; this is UI-only.
 */
const DURATION_RE = /^(?:(?<days>\d+)d)?(?:(?<hours>\d+)h)?(?:(?<minutes>\d+)m)?$/;

/** Validate a duration string against the grammar. Returns `true` if the string is non-empty and
 * matches the d/h/m pattern with at least one unit (mirrors Python `parse_duration` error cases). */
export function isValidDuration(text: string): boolean {
  const candidate = text.trim();
  if (candidate === '') {
    return false;
  }
  const match = DURATION_RE.exec(candidate);
  if (match === null) {
    return false;
  }
  // At least one group must be non-undefined (all-optional regex matches empty — guard like Python).
  // `noPropertyAccessFromIndexSignature` (strict tsconfig) requires bracket notation; Biome's
  // auto-fix to dot-notation would break the build — suppress intentionally.
  const groups = match.groups ?? {};
  // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature forces bracket notation; dot-notation fix breaks tsc strict build.
  const hasDays = groups['days'] !== undefined;
  // biome-ignore lint/complexity/useLiteralKeys: same — bracket notation required by tsconfig strict
  const hasHours = groups['hours'] !== undefined;
  // biome-ignore lint/complexity/useLiteralKeys: same — bracket notation required by tsconfig strict
  const hasMinutes = groups['minutes'] !== undefined;
  return hasDays || hasHours || hasMinutes;
}

// ── Actions ─────────────────────────────────────────────────────────────────────────────────────

/** The ticket-detail actions, bound to one `BusClient` + store handle. */
export interface TicketDetailActions {
  /**
   * Load the detail for a ticket and open the editor slice. Transitions status:
   * idle → loading → ready (on success) or error (on rejection).
   * Sets `ticketId`, `frontmatter`, `savedBody`, `editedBody` (= savedBody), `scheduleInput ''`.
   */
  open(ticketId: string): Promise<void>;
  /**
   * Close the editor slice — resets to `initialTicketDetailState` (idle, all nulls).
   * Called when the editor mode exits without saving, or after a successful save.
   */
  close(): void;
  /**
   * Update the in-progress editor buffer (the live edit; does NOT call the bus).
   * The component calls this as the user types; `savedBody` is unchanged until `saveBody()`.
   */
  setEditedBody(body: string): void;
  /**
   * Update the schedule input field (does NOT call the bus; updates `scheduleValid` inline).
   */
  setScheduleInput(value: string): void;
  /**
   * Persist the edited body to the service via `ticket.save_body`. Only calls the bus if
   * `editedBody != null` and a ticket is open. Ref-swaps `savedBody` = `editedBody` on success.
   */
  saveBody(): Promise<void>;
  /**
   * Send the schedule input to the service via `ticket.schedule`. Only calls the bus if
   * `scheduleInput` is valid and a ticket is open. Clears `scheduleInput` on success.
   */
  schedule(): Promise<void>;
}

function toFrontmatter(dto: TicketDetailReply): TicketFrontmatter {
  return {
    title: dto.title,
    status: dto.status,
    // Python sends pending dep ids as an array; join to a comma string for the header.
    deps: (dto.deps ?? []).join(', '),
    harness: dto.harness ?? null,
    model: dto.model ?? null,
    worktree: dto.worktree ?? null,
    scheduleAt: dto.schedule_at ?? null,
  };
}

export function createTicketDetailActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): TicketDetailActions {
  return {
    async open(ticketId: string): Promise<void> {
      store.setState((state) => ({
        ticketDetail: {
          ...state.ticketDetail,
          ticketId,
          frontmatter: null,
          savedBody: null,
          editedBody: null,
          scheduleInput: '',
          scheduleValid: false,
          status: 'loading',
          error: null,
        },
      }));
      try {
        const reply = await bus.query('ticket.get', { ticket_id: ticketId });
        store.setState((state) => {
          // Stale-reply guard: a slow `open(A)` must NOT overwrite the slice once the user has
          // opened/closed to a different ticket. Open A (slow) → escape + open B (fast); B resolves,
          // then A resolves — without this check A's body lands under B's identity (silent corruption,
          // mirrors the docView identity check). The slice's own `ticketId` is the identity.
          if (state.ticketDetail.ticketId !== ticketId) {
            return state;
          }
          if (reply === null) {
            return {
              ticketDetail: {
                ...state.ticketDetail,
                frontmatter: null,
                savedBody: null,
                editedBody: null,
                status: 'error',
                error: `ticket not found: ${ticketId}`,
              },
            };
          }
          const next: TicketDetailState = {
            ticketId,
            frontmatter: toFrontmatter(reply),
            savedBody: reply.body,
            editedBody: reply.body,
            scheduleInput: '',
            scheduleValid: false,
            status: 'ready',
            error: null,
          };
          return { ticketDetail: next };
        });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => {
          if (state.ticketDetail.ticketId !== ticketId) {
            return state;
          }
          return { ticketDetail: { ...state.ticketDetail, status: 'error', error: message } };
        });
      }
    },

    close(): void {
      store.setState((state) => ({
        ticketDetail: {
          ...state.ticketDetail,
          ticketId: null,
          frontmatter: null,
          savedBody: null,
          editedBody: null,
          scheduleInput: '',
          scheduleValid: false,
          status: 'idle',
          error: null,
        },
      }));
    },

    setEditedBody(body: string): void {
      store.setState((state) => ({
        ticketDetail: { ...state.ticketDetail, editedBody: body },
      }));
    },

    setScheduleInput(value: string): void {
      store.setState((state) => ({
        ticketDetail: {
          ...state.ticketDetail,
          scheduleInput: value,
          scheduleValid: isValidDuration(value),
        },
      }));
    },

    async saveBody(): Promise<void> {
      const { ticketId, editedBody } = store.getState().ticketDetail;
      if (ticketId === null || editedBody === null) {
        return;
      }
      store.setState((state) => ({ ticketDetail: { ...state.ticketDetail, status: 'saving' } }));
      try {
        const reply = await bus.command('ticket.save_body', {
          ticket_id: ticketId,
          body: editedBody,
        });
        // SOFT-FAIL guard: the service can resolve (not reject) with `{ok:false, error}` (e.g.
        // ticket not found). Without this check that reply takes the success branch → the user
        // thinks the body saved when it did not (silent data loss). Route it to the SAME error
        // path as a thrown rejection: slice `error` + the global error toast (the landed write-RPC
        // surfacing mechanism, commit 73d7110).
        if (reply.ok === false) {
          const message = reply.error ?? 'save failed';
          store.setState((state) => ({
            ticketDetail: { ...state.ticketDetail, status: 'error', error: message },
          }));
          toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
          return;
        }
        store.setState((state) => ({
          ticketDetail: {
            ...state.ticketDetail,
            savedBody: editedBody,
            status: 'ready',
            error: null,
          },
        }));
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          ticketDetail: { ...state.ticketDetail, status: 'error', error: message },
        }));
      }
    },

    async schedule(): Promise<void> {
      const { ticketId, scheduleInput, scheduleValid } = store.getState().ticketDetail;
      if (ticketId === null || !scheduleValid) {
        return;
      }
      try {
        await bus.command('ticket.schedule', {
          ticket_id: ticketId,
          duration: scheduleInput.trim(),
        });
        store.setState((state) => ({
          ticketDetail: {
            ...state.ticketDetail,
            scheduleInput: '',
            scheduleValid: false,
            error: null,
          },
        }));
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          ticketDetail: { ...state.ticketDetail, error: message },
        }));
      }
    },
  };
}
