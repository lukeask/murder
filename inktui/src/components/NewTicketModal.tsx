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
 * `actions.quickCreateTicket` dispatches the `ticket.quick_create` command kind through the LIVE
 * `command.submit` choke point (handled by `orchestrator_worker.py`).
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { applyEditorKey } from '../input/textEditor/applyEditorKey.js';
import { singleLineEditorPolicy } from '../input/textEditor/keyDecoder.js';
import { reduceEditor } from '../input/textEditor/operations.js';
import { plainTextProjection } from '../input/textEditor/projection.js';
import { editorAtEnd, type TextEditorState } from '../input/textEditor/state.js';
import { plainTextTopology } from '../input/textEditor/topology.js';
import type { DialogActions } from '../store/dialogs/dialogActions.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { TextEditorDisplay } from './TextEditorDisplay.js';

/** Content width shared by the title field renderer and visual-motion geometry. */
const TITLE_EDITOR_WIDTH = 54;

// Import the dispatcher augmentation so Mode gets the `onUncaptured` field at the TS level.
import '../input/dispatcher.js';

/** Intent union for the new-ticket dialog — special key actions only. */
type NewTicketIntent =
  | 'backspace'
  | 'deleteForward'
  | 'moveLeft'
  | 'moveRight'
  | 'moveUp'
  | 'moveDown'
  | 'home'
  | 'end'
  | 'deleteAll'
  | 'submit'
  | 'dismiss';

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
  title: TextEditorState;
  error: string | null;
}

/**
 * Build the new-ticket {@link Mode}. Enter via:
 * `modes.getState().enter(newTicketMode(modes, actions, {}))`.
 *
 * The mode is self-dismissing: `submit` calls `modes.exit(id)` before the async RPC
 * (exit-then-act — same as ConfirmModal and NewPlanModal).
 *
 * `actions.quickCreateTicket` → `ticket.quick_create` command kind via the LIVE `command.submit`.
 */
export function newTicketMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewTicketModeOptions = {},
): Mode<NewTicketIntent> {
  const id = NEW_TICKET_MODE_ID;

  // Mutable local state in the closure — not React state.
  const s: NewTicketState = {
    title: editorAtEnd(),
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
      { chord: { key: { delete: true } }, intent: 'deleteForward', description: 'delete' },
      { chord: { key: { leftArrow: true } }, intent: 'moveLeft', description: 'left' },
      { chord: { key: { rightArrow: true } }, intent: 'moveRight', description: 'right' },
      { chord: { key: { upArrow: true } }, intent: 'moveUp', description: 'up' },
      { chord: { key: { downArrow: true } }, intent: 'moveDown', description: 'down' },
      { chord: { key: { home: true } }, intent: 'home', description: 'line start' },
      { chord: { key: { end: true } }, intent: 'end', description: 'line end' },
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
          s.title = editTicket(s.title, { type: 'backspace' });
          refresh();
          break;
        }
        case 'deleteForward':
          s.title = editTicket(s.title, { type: 'deleteForward' });
          refresh();
          break;
        case 'moveLeft':
          s.title = editTicket(s.title, { type: 'moveLeft' });
          refresh();
          break;
        case 'moveRight':
          s.title = editTicket(s.title, { type: 'moveRight' });
          refresh();
          break;
        case 'moveUp':
          s.title = editTicket(s.title, { type: 'moveVisualUp' });
          refresh();
          break;
        case 'moveDown':
          s.title = editTicket(s.title, { type: 'moveVisualDown' });
          refresh();
          break;
        case 'home':
          s.title = editTicket(s.title, { type: 'moveLineStart' });
          refresh();
          break;
        case 'end':
          s.title = editTicket(s.title, { type: 'moveLineEnd' });
          refresh();
          break;
        case 'deleteAll': {
          s.title = editorAtEnd();
          refresh();
          break;
        }
        case 'submit': {
          if (s.title.text.trim().length === 0) {
            s.error = 'Ticket title is required.';
            refresh();
            break;
          }
          // Exit-then-act: exit (restores focus) before the async RPC.
          modes.getState().exit(id);
          const title = s.title.text.trim();
          void actions
            .quickCreateTicket(title)
            .then((result) => {
              opts.onSubmit?.(result.ticket_id, result.title);
            })
            .catch((error: unknown) => {
              // Exit-then-act: the modal is already gone and focus restored, so an inline field
              // error has nowhere to render. Surface the action-level RPC rejection on the global
              // toastStore (a singleton, independent of this unmounted modal's lifecycle), using the
              // structured `rpc error [code]: message` text from ApplicationWebSocketClient's rejection.
              const message = error instanceof Error ? error.message : String(error);
              toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
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
    // onUncaptured: shared decoder rejects Tab/Esc and maps printables to insert commands.
    onUncaptured(input: string, key: Key): boolean {
      const transition = applyEditorKey(s.title, input, key, {
        policy: singleLineEditorPolicy,
        environment: {
          width: TITLE_EDITOR_WIDTH,
          topology: plainTextTopology,
          projection: plainTextProjection,
        },
      });
      if (transition === null) return false;
      s.title = transition.state;
      s.error = null;
      refresh();
      return true;
    },
    render: () => <NewTicketDialog title={s.title} error={s.error} />,
  };

  return mode;
}

function editTicket(
  state: TextEditorState,
  command: import('../input/textEditor/commands.js').EditorCommand,
): TextEditorState {
  return reduceEditor(state, command, {
    width: TITLE_EDITOR_WIDTH,
    topology: plainTextTopology,
    projection: plainTextProjection,
  }).state;
}

/** The dialog's presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function NewTicketDialog({
  title,
  error,
}: {
  readonly title: TextEditorState;
  readonly error: string | null;
}): JSX.Element {
  const theme = useTheme();
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
        <TextEditorDisplay
          state={title}
          width={TITLE_EDITOR_WIDTH}
          placeholder="Short description of the work…"
          focused
        />
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
