/**
 * `NewTicketModal` — the `:ticket` new-ticket popup: a **modal C7M mode** that presents a title
 * field for a new ticket.
 *
 * ## Launch path
 *
 * When {@link NewTicketModeOptions.preferBuiltinTicket} is set (configured harness/model defaults
 * make the built-in ``ticket`` workflow runnable), submit fires ``workflow.start`` via
 * {@link DialogActions.startBuiltinTicket}. Otherwise it keeps the temporary
 * ``ticket.quick_create`` fallback for unconfigured planned tickets.
 *
 * This dialog stays in-TUI (rule 1 — no `$EDITOR`-blank).
 *
 * ## C13 copy recipe
 *
 * This dialog is a single-field modal. Copy it alongside {@link NewPlanModal} for multi-field
 * dialogs; the pattern is identical:
 *  - Intent union for special keys; printable chars via `onUncaptured` (the C12 dispatcher ext).
 *  - Mutable closure state + `refresh()` to re-render.
 *  - Exit-then-act in `submit`.
 *  - Pure presentational component in `render`.
 */

import type { Key } from 'ink';
import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { applyEditorKey } from '../input/textEditor/applyEditorKey.js';
import { multilineEditorPolicy, singleLineEditorPolicy } from '../input/textEditor/keyDecoder.js';
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
const PROMPT_EDITOR_WIDTH = 54;

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
  | 'focusNext'
  | 'focusPrev'
  | 'newline'
  | 'submit'
  | 'dismiss';

type FocusField = 'title' | 'prompt';

/** Options passed to the mode factory. */
export interface NewTicketModeOptions {
  /** Called with the new ticket's id + title after a successful submit (fired after mode exits). */
  readonly onSubmit?: (ticketId: string, title: string) => void;
  /** Called when the dialog is dismissed without submitting (fired after mode exits). */
  readonly onDismiss?: () => void;
  /**
   * When true, submit launches the built-in ``ticket`` workflow (title + optional instructions).
   * When false/omitted, falls back to ``ticket.quick_create`` (title only).
   */
  readonly preferBuiltinTicket?: boolean;
}

/** The stable mode id so a re-enter is idempotent. */
export const NEW_TICKET_MODE_ID = 'new-ticket';

/** Mutable local state inside the mode closure. Not React state — the mode is plain data. */
interface NewTicketState {
  title: TextEditorState;
  prompt: TextEditorState;
  focus: FocusField;
  error: string | null;
}

/**
 * Build the new-ticket {@link Mode}. Enter via:
 * `modes.getState().enter(newTicketMode(modes, actions, {}))`.
 *
 * The mode is self-dismissing: `submit` calls `modes.exit(id)` before the async RPC
 * (exit-then-act — same as ConfirmModal and NewPlanModal).
 */
export function newTicketMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewTicketModeOptions = {},
): Mode<NewTicketIntent> {
  const id = NEW_TICKET_MODE_ID;
  const preferBuiltin = opts.preferBuiltinTicket === true;

  // Mutable local state in the closure — not React state.
  const s: NewTicketState = {
    title: editorAtEnd(),
    prompt: editorAtEnd(),
    focus: 'title',
    error: null,
  };

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  function activeEditor(): TextEditorState {
    return s.focus === 'title' ? s.title : s.prompt;
  }

  function setActiveEditor(next: TextEditorState): void {
    if (s.focus === 'title') s.title = next;
    else s.prompt = next;
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
      ...(preferBuiltin
        ? ([
            {
              chord: { key: { tab: true } },
              intent: 'focusNext' as const,
              description: 'next field',
            },
            {
              chord: { key: { shift: true, tab: true } },
              intent: 'focusPrev' as const,
              description: 'prev field',
            },
          ] as const)
        : []),
      // Shift+Enter must precede bare Enter: unlisted shift is don't-care, first-match-wins.
      // Inserts a newline in Instructions; no-op while Title is focused.
      { chord: { key: { shift: true, return: true } }, intent: 'newline', description: 'newline' },
      // Enter: submit.
      { chord: { key: { return: true } }, intent: 'submit', description: 'create ticket' },
      // Escape: dismiss.
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'backspace': {
          setActiveEditor(editTicket(activeEditor(), { type: 'backspace' }, s.focus));
          refresh();
          break;
        }
        case 'deleteForward':
          setActiveEditor(editTicket(activeEditor(), { type: 'deleteForward' }, s.focus));
          refresh();
          break;
        case 'moveLeft':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveLeft' }, s.focus));
          refresh();
          break;
        case 'moveRight':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveRight' }, s.focus));
          refresh();
          break;
        case 'moveUp':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveVisualUp' }, s.focus));
          refresh();
          break;
        case 'moveDown':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveVisualDown' }, s.focus));
          refresh();
          break;
        case 'home':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveLineStart' }, s.focus));
          refresh();
          break;
        case 'end':
          setActiveEditor(editTicket(activeEditor(), { type: 'moveLineEnd' }, s.focus));
          refresh();
          break;
        case 'deleteAll': {
          setActiveEditor(editorAtEnd());
          refresh();
          break;
        }
        case 'focusNext':
          if (preferBuiltin) {
            s.focus = s.focus === 'title' ? 'prompt' : 'title';
            refresh();
          }
          break;
        case 'focusPrev':
          if (preferBuiltin) {
            s.focus = s.focus === 'title' ? 'prompt' : 'title';
            refresh();
          }
          break;
        case 'newline': {
          if (s.focus === 'prompt') {
            setActiveEditor(editTicket(activeEditor(), { type: 'insertNewline' }, s.focus));
            refresh();
          }
          break;
        }
        case 'submit': {
          if (s.title.text.trim().length === 0) {
            s.error = 'Ticket title is required.';
            s.focus = 'title';
            refresh();
            break;
          }
          // Exit-then-act: exit (restores focus) before the async RPC.
          modes.getState().exit(id);
          const title = s.title.text.trim();
          const prompt = s.prompt.text;
          const create = preferBuiltin
            ? actions.startBuiltinTicket({ title, prompt }).then((result) => ({
                ticket_id: result.ticket_id,
                title: result.title,
              }))
            : actions.quickCreateTicket(title).then((result) => ({
                ticket_id: result.ticket_id,
                title: result.title,
              }));
          void create
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
      const width = s.focus === 'title' ? TITLE_EDITOR_WIDTH : PROMPT_EDITOR_WIDTH;
      const transition = applyEditorKey(activeEditor(), input, key, {
        policy: s.focus === 'title' ? singleLineEditorPolicy : multilineEditorPolicy,
        environment: {
          width,
          topology: plainTextTopology,
          projection: plainTextProjection,
        },
      });
      if (transition === null) return false;
      setActiveEditor(transition.state);
      s.error = null;
      refresh();
      return true;
    },
    render: () => (
      <NewTicketDialog
        title={s.title}
        prompt={s.prompt}
        focus={s.focus}
        error={s.error}
        showPrompt={preferBuiltin}
      />
    ),
  };

  return mode;
}

function editTicket(
  state: TextEditorState,
  command: import('../input/textEditor/commands.js').EditorCommand,
  focus: FocusField,
): TextEditorState {
  const width = focus === 'title' ? TITLE_EDITOR_WIDTH : PROMPT_EDITOR_WIDTH;
  return reduceEditor(state, command, {
    width,
    topology: plainTextTopology,
    projection: plainTextProjection,
  }).state;
}

/** The dialog's presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function NewTicketDialog({
  title,
  prompt,
  focus,
  error,
  showPrompt,
}: {
  readonly title: TextEditorState;
  readonly prompt: TextEditorState;
  readonly focus: FocusField;
  readonly error: string | null;
  readonly showPrompt: boolean;
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
        <Text color={theme.text}>Title:</Text>
        <TextEditorDisplay
          state={title}
          width={TITLE_EDITOR_WIDTH}
          placeholder="Short description of the work…"
          focused={focus === 'title'}
        />
      </Box>
      {showPrompt ? (
        <Box marginTop={1} flexDirection="column">
          <Text color={theme.text}>Instructions:</Text>
          <TextEditorDisplay
            state={prompt}
            width={PROMPT_EDITOR_WIDTH}
            placeholder="Optional brief for the agent…"
            focused={focus === 'prompt'}
          />
        </Box>
      ) : null}
      {error !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>{error}</Text>
        </Box>
      )}
      <Box marginTop={1}>
        <Text color={theme.muted}>
          {showPrompt
            ? 'enter: start  shift+enter: newline  tab: field  esc: cancel  alt+u: clear'
            : 'enter: create esc: cancel alt+u: clear'}
        </Text>
      </Box>
    </Box>
  );
}
