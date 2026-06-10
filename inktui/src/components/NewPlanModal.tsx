/**
 * `NewPlanModal` — the `ctrl+p` new-plan popup: a **modal C7M mode** with two text fields:
 * a plan name and an initial message to send to a fresh planning agent.
 *
 * ## Recipe (C12 modal pattern — the reference C13 copies)
 *
 * Copy this file alongside {@link TextInput} and {@link ../store/dialogs/dialogActions.js} to build a
 * new multi-field dialog:
 *
 *  1. **Define an intent union** for every special key action in the dialog (`backspace`,
 *     `deleteAll`, `nextField`, `prevField`, `submit`, `dismiss`). Printable characters are
 *     routed through `onUncaptured` (the C12 dispatcher extension — not the keymap), which lets
 *     the mode handle raw chars the keymap does not declare without any keymap wildcard entry.
 *  2. **Build the mode with `newPlanMode(modes, actions, opts)`** — a factory that returns a
 *     {@link Mode} with: `presentation: 'modal'`, a declared keymap, an `onIntent` for special
 *     keys, an `onUncaptured` for printable characters, and a `render` thunk over a pure component.
 *  3. **Enter it** from a global chord handler:
 *     `modes.getState().enter(newPlanMode(modes, actions, {}))`.
 *  4. The {@link ../components/Overlay.js Overlay} centers it; the C7M primitive handles
 *     capture and focus restore — the consumer writes none of that.
 *
 * ## Field + state model
 *
 * Modal state (field values, focused field index, error, submitting) lives in a mutable object
 * inside the mode factory closure — not in React state (the mode is plain data, not a component).
 * The `render` thunk closes over the object; after each mutation the mode store is poked (re-enter
 * same id → new stack ref → Zustand re-renders subscribers). C13 uses the same pattern.
 *
 * ## `passThrough: false` (default)
 *
 * The modal captures every key while up — global chords cannot fire underneath it. `onUncaptured`
 * handles printable chars before the swallow decision in the dispatcher.
 *
 * **B13 flag:** `actions.createPlan` calls `plan.create` — not yet live on the bus.
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
// The augmentation is declared in dispatcher.ts; importing it brings the declaration into scope.
import '../input/dispatcher.js';

/** Intent union for the new-plan dialog — special key actions only. Printable chars go through
 * `onUncaptured`, not the keymap, so they are not listed here. */
type NewPlanIntent = 'backspace' | 'deleteAll' | 'nextField' | 'prevField' | 'submit' | 'dismiss';

/** Options passed to the mode factory. */
export interface NewPlanModeOptions {
  /** Called with the plan name and message after a successful submit (fired after mode exits). */
  readonly onSubmit?: (planName: string, message: string) => void;
  /** Called when the dialog is dismissed without submitting (fired after mode exits). */
  readonly onDismiss?: () => void;
}

/** The stable mode id so a re-enter is idempotent. */
export const NEW_PLAN_MODE_ID = 'new-plan';

/** The number of fields in the new-plan dialog. */
const FIELD_COUNT = 2;
/** Field indices. */
const FIELD_PLAN_NAME = 0;
const FIELD_MESSAGE = 1;

/**
 * Mutable local state inside the mode closure. Not React state — the mode is plain data.
 * Mutated in `onIntent` / `onUncaptured`; `render` reads it at call time.
 */
interface NewPlanState {
  planName: string;
  message: string;
  activeField: number;
  error: string | null;
}

/**
 * Build the new-plan {@link Mode}. Pass `modes` (for self-dismiss), `actions` (for the RPC), and
 * optional callbacks. The mode is self-dismissing: `submit` calls `modes.exit(id)` before the
 * async RPC (exit-then-act, same order as ConfirmModal so the restore happens first).
 *
 * Enter via: `modes.getState().enter(newPlanMode(modes, actions, {}))`.
 * The global chord handler in `useRootInput` calls this when `ctrl+p` fires.
 *
 * **B13 flag:** `actions.createPlan` → `plan.create` not yet on the live bus.
 */
export function newPlanMode(
  modes: ModeStoreApi,
  actions: DialogActions,
  opts: NewPlanModeOptions = {},
): Mode<NewPlanIntent> {
  const id = NEW_PLAN_MODE_ID;

  // Mutable local state in the closure — not React state.
  const s: NewPlanState = {
    planName: '',
    message: '',
    activeField: FIELD_PLAN_NAME,
    error: null,
  };

  // Re-render by poking the mode store: re-enter the same id (idempotent focus, new stack ref).
  function refresh(): void {
    const current = modes.getState().stack.find((f) => f.mode.id === id);
    if (current !== undefined) {
      modes.getState().enter(current.mode);
    }
  }

  const mode: Mode<NewPlanIntent> = {
    id,
    presentation: 'modal',
    // No pass-through: the dialog captures every key while up.
    keymap: [
      // Tab / Shift-Tab: cycle fields.
      { chord: { key: { tab: true } }, intent: 'nextField', description: 'next field' },
      {
        chord: { key: { shift: true, tab: true } },
        intent: 'prevField',
        description: 'prev field',
      },
      // Backspace: delete last char.
      { chord: { key: { backspace: true } }, intent: 'backspace', description: 'delete char' },
      // Alt+U: clear field.
      {
        chord: { input: 'u', key: { meta: true } },
        intent: 'deleteAll',
        description: 'clear field',
      },
      // Enter: submit.
      { chord: { key: { return: true } }, intent: 'submit', description: 'create plan' },
      // Escape: dismiss.
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      switch (intent) {
        case 'backspace': {
          if (s.activeField === FIELD_PLAN_NAME) {
            s.planName = deleteLastChar(s.planName);
          } else {
            s.message = deleteLastChar(s.message);
          }
          refresh();
          break;
        }
        case 'deleteAll': {
          if (s.activeField === FIELD_PLAN_NAME) {
            s.planName = '';
          } else {
            s.message = '';
          }
          refresh();
          break;
        }
        case 'nextField': {
          s.activeField = (s.activeField + 1) % FIELD_COUNT;
          refresh();
          break;
        }
        case 'prevField': {
          s.activeField = (s.activeField - 1 + FIELD_COUNT) % FIELD_COUNT;
          refresh();
          break;
        }
        case 'submit': {
          if (s.planName.trim().length === 0) {
            s.error = 'Plan name is required.';
            refresh();
            break;
          }
          // Exit-then-act: exit (restores focus) before the async RPC, per ConfirmModal precedent.
          modes.getState().exit(id);
          const planName = s.planName.trim();
          const message = s.message.trim();
          void actions
            .createPlan(planName, message)
            .then(() => {
              opts.onSubmit?.(planName, message);
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
    // onUncaptured: handle printable characters for text field input (C12 dispatcher extension).
    // The dispatcher calls this when the keymap has no match. We accept printable chars (non-empty
    // input string, no ctrl/alt modifiers) and append them to the active field.
    onUncaptured(input: string, key: Key): boolean {
      if (input.length === 0 || key.ctrl || key.meta || key.escape) {
        return false; // special key — let the dispatcher swallow it (not our char to handle)
      }
      if (s.activeField === FIELD_PLAN_NAME) {
        s.planName = insertChar(s.planName, input);
      } else {
        s.message = insertChar(s.message, input);
      }
      s.error = null;
      refresh();
      return true;
    },
    render: () => (
      <NewPlanDialog
        planName={s.planName}
        message={s.message}
        activeField={s.activeField}
        error={s.error}
      />
    ),
  };

  return mode;
}

/** The dialog's visual presentation — a pure function of its props (rule 1). No store/bus knowledge. */
function NewPlanDialog({
  planName,
  message,
  activeField,
  error,
}: {
  readonly planName: string;
  readonly message: string;
  readonly activeField: number;
  readonly error: string | null;
}): JSX.Element {
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.heading}
      paddingX={2}
      paddingY={1}
      width={60}
    >
      <Text bold color={theme.heading}>
        New Plan
      </Text>
      <Box marginTop={1} flexDirection="column">
        <Text color={activeField === FIELD_PLAN_NAME ? theme.text : theme.muted}>Plan name:</Text>
        <TextInput
          value={planName}
          placeholder="e.g. refactor-auth"
          focused={activeField === FIELD_PLAN_NAME}
          color={activeField === FIELD_PLAN_NAME ? theme.text : theme.muted}
        />
      </Box>
      <Box marginTop={1} flexDirection="column">
        <Text color={activeField === FIELD_MESSAGE ? theme.text : theme.muted}>
          Message to planning agent:
        </Text>
        <TextInput
          value={message}
          placeholder="Describe the plan goal…"
          focused={activeField === FIELD_MESSAGE}
          color={activeField === FIELD_MESSAGE ? theme.text : theme.muted}
        />
      </Box>
      {error !== null && (
        <Box marginTop={1}>
          <Text color={theme.error}>{error}</Text>
        </Box>
      )}
      <Box marginTop={1}>
        <Text dimColor>tab: next field enter: create esc: cancel ctrl+u: clear</Text>
      </Box>
    </Box>
  );
}
