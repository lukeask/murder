/**
 * `NewTicketModal` — the `ctrl+t` new-ticket popup: a **modal C7M mode** that presents a title
 * field for a new ticket, analogous to the old `:ticket` flow.
 *
 * ## What this delivers vs. `:ticket`
 *
 * The old `:ticket` flow required opening a file editor, blanking the screen. This dialog stays
 * in-TUI (rule 1 — no `$EDITOR`-blank) and fires `ticket.quick_create {title}` (rule 3).
 * On submit, the service creates the ticket and returns its id. The result is delivered to the
 * caller's `onSubmit` callback; the component itself never stores the ticket id.
 *
 * ## C13 copy recipe
 *
 * This dialog is a single-field modal. Copy it alongside {@link NewPlanModal} for multi-field
 * dialogs; the pattern is identical:
 *  - Intent union for special keys; printable chars via `onUncaptured` (the C12 dispatcher ext).
 *  - Mutable closure state + `refresh()` to re-render.
 *  - Exit-then-act in `submit`.
 *  - Pure presentational component in `render`.
 *
 * **B13 flag:** `actions.quickCreateTicket` calls `ticket.quick_create` — not yet live on the bus.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import type { DialogActions } from '../store/dialogs/dialogActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { theme } from '../theme.js';
import { deleteLastChar, insertChar, TextInput } from './TextInput.js';

// Import the dispatcher augmentation so Mode gets the `onUncaptured` field at the TS level.
import '../input/dispatcher.js';

/** Intent union for the new-ticket dialog — special key actions only. */
type NewTicketIntent = 'backspace' | 'deleteAll' | 'submit' | 'dismiss';

/** Options passed to the mode factory. */
export interface NewTicketModeOptions {
  /** Called with the new ticket's id + title after a successful submit (fired after mode exits). */
  readonly onSubmit?: (ticketId: string, title: string) => void;
  /** Called when the dialog is dismissed without submitting (fired after mode exits). */
  readonly onDismiss?: () => void;
}

/** The stable mode id so a re-enter is idempotent. */
export const NEW_TICKET_MODE_ID = 'new-ticket';

/** Mutable local state inside the mode closure. Not React state — the mode is plain data. */
interface NewTicketState {
  title: string;
  error: string | null;
}

/**
 * Build the new-ticket {@link Mode}. Enter via:
 * `modes.getState().enter(newTicketMode(modes, actions, {}))`.
 *
 * The mode is self-dismissing: `submit` calls `modes.exit(id)` before the async RPC
 * (exit-then-act — same as ConfirmModal and NewPlanModal).
 *
 * **B13 flag:** `actions.quickCreateTicket` → `ticket.quick_create` not yet on the live bus.
 */
export function newTicketMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewTicketModeOptions = {},
): Mode<NewTicketIntent> {
  const id = NEW_TICKET_MODE_ID;

  // Mutable local state in the closure — not React state.
  const s: NewTicketState = {
    title: '',
    error: null,
  };

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  const mode: Mode<NewTicketIntent> = {
    id,
    presentation: 'modal',
    // No pass-through: the dialog captures every key while up.
    keymap: [
      // Backspace: delete last char.
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      // Alt+U: clear field.
      {
        chord: { input: 'u', key: { meta: true } },
        intent: 'deleteAll',
        description: 'clear field',
      },
      // Enter: submit.
      { chord: { key: { return: true } }, intent: 'submit', description: 'create ticket' },
      // Escape: dismiss.
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'backspace': {
          s.title = deleteLastChar(s.title);
          refresh();
          break;
        }
        case 'deleteAll': {
          s.title = '';
          refresh();
          break;
        }
        case 'submit': {
          if (s.title.trim().length === 0) {
            s.error = 'Ticket title is required.';
            refresh();
            break;
          }
          // Exit-then-act: exit (restores focus) before the async RPC.
          modes.getState().exit(id);
          const title = s.title.trim();
          void actions
            .quickCreateTicket(title)
            .then((result) => {
              opts.onSubmit?.(result.ticket_id, result.title);
            })
            .catch((error: unknown) => {
              // Exit-then-act: the modal is already gone and focus restored, so an inline field
              // error has nowhere to render. Surface the action-level RPC rejection on the global
              // toastStore (a singleton, independent of this unmounted modal's lifecycle), using the
              // structured `rpc error [code]: message` text from UdsBusClient's rejection.
              const message = error instanceof Error ? error.message : String(error);
              toastStore.getState().push(message, { severity: 'error', ttlMs: 6000 });
            });
          break;
        }
        case 'dismiss': {
          modes.getState().exit(id);
          opts.onDismiss?.();
          break;
        }
        default:
          return intent satisfies never;
      }
    },
    // onUncaptured: handle printable characters (C12 dispatcher extension).
    onUncaptured(input: string, key: Key): boolean {
      if (input.length === 0 || key.ctrl || key.meta || key.escape) {
        return false;
      }
      s.title = insertChar(s.title, input);
      s.error = null;
      refresh();
      return true;
    },
    render: () => <NewTicketDialog title={s.title} error={s.error} />,
  };

  return mode;
}

/** The dialog's presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function NewTicketDialog({
  title,
  error,
}: {
  readonly title: string;
  readonly error: string | null;
}): JSX.Element {
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.success}
      paddingX={2}
      paddingY={1}
      width={60}
    >
      <Text bold color={theme.success}>
        New Ticket
      </Text>
      <Box marginTop={1} flexDirection="column">
        <Text>Title:</Text>
        <TextInput value={title} placeholder="Short description of the work…" focused={true} />
      </Box>
      {error !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>{error}</Text>
        </Box>
      )}
      <Box marginTop={1}>
        <Text dimColor>enter: create esc: cancel ctrl+u: clear</Text>
      </Box>
    </Box>
  );
}
