/**
 * ConfirmModal — THE reference transient mode. A minimal yes/no confirm dialog wired end-to-end
 * through the C7M primitive, so the three modal-ish chunks (C8 editor, C12 dialogs, C14 tmux) copy a
 * *complete, correct* pattern rather than invent their own. There are no stub TODOs here on purpose:
 * a reference that later agents copy must be finished, or it propagates the gap (DoD rule 3).
 *
 * What it demonstrates (the recipe — see also the file header of {@link ../input/modeStore.js}):
 *  1. **Declare a mode as data** — {@link confirmMode} builds a {@link Mode}: an `id`, a `presentation`
 *     (here `'modal'` — a centered popup), a *declared* keymap (`y`→confirm, `n`/`Esc`→dismiss; the
 *     dismiss key is just another declared chord, no special-cased Escape in the dispatcher), an
 *     `onIntent` that runs the choice and exits, and a thin `render` that draws the box.
 *  2. **Enter it** — a trigger (a panel intent, a global chord, a dev button) calls
 *     `modes.getState().enter(confirmMode({ ... }))`. Entering saves the current focus.
 *  3. **It captures input** — while up, the dispatcher's layer 0 routes every key to this mode's
 *     keymap only (global chords + panels are suppressed; no pass-through declared here).
 *  4. **Dismiss restores focus** — `onIntent` calls `exit(id)`, which pops the mode and restores the
 *     focus that was live when it opened. The consumer writes none of that — it is the primitive's job.
 *
 * The render is a pure presentational component (rule 1): it takes its message/labels as props and
 * has no store/bus knowledge. C12's dialog body, C8's editor, and C14's frame each replace *this
 * render and this keymap* while reusing the enter/exit/capture/restore machinery unchanged.
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';
import { useTheme } from '../theme/themeStore.js';

/** The choice a confirm dialog resolves to. */
export type ConfirmChoice = 'confirm' | 'dismiss';

/** What the caller supplies to raise a confirm dialog. The render is built from `message`; `onChoose`
 * is the caller's callback run with the resolved choice as the mode exits. `id` lets a caller raise
 * multiple distinct confirms (defaults to a single shared id — re-entering is idempotent). */
export interface ConfirmModeOptions {
  readonly message: string;
  readonly onChoose: (choice: ConfirmChoice) => void;
  readonly id?: string;
  /** Label for the confirm (`y`) action; defaults to `'Yes'`. */
  readonly confirmLabel?: string;
  /** Label for the dismiss (`n`/Esc) action; defaults to `'No'`. */
  readonly dismissLabel?: string;
}

/** The confirm dialog's intent union — its own action names, so {@link confirmMode}'s `onIntent` is
 * exhaustively typed against the keymap it declares. */
type ConfirmIntent = ConfirmChoice;

/** The default mode id, so the common single-confirm case needs no id and re-entry stays idempotent. */
const DEFAULT_CONFIRM_ID = 'confirm';

/**
 * Build the confirm {@link Mode}. Pass the `modes` store handle so the mode can `exit` itself when a
 * choice is made — this is the canonical shape of a self-dismissing mode (the consumer hands the
 * store in; the mode owns its own dismissal). The resolved choice is delivered to `onChoose` as the
 * mode exits, and exiting restores prior focus via the primitive.
 */
export function confirmMode(modes: ModeStoreApi, options: ConfirmModeOptions): Mode<ConfirmIntent> {
  const id = options.id ?? DEFAULT_CONFIRM_ID;
  const confirmLabel = options.confirmLabel ?? 'Yes';
  const dismissLabel = options.dismissLabel ?? 'No';
  return {
    id,
    presentation: 'modal',
    // Declared keymap: y → confirm, n or Esc → dismiss. No pass-through, so the dialog captures
    // every key while up (a stray `ctrl+1` can't summon a panel behind it).
    keymap: [
      { chord: { input: 'y' }, intent: 'confirm', description: confirmLabel },
      { chord: { input: 'n' }, intent: 'dismiss', description: dismissLabel },
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
    ],
    onIntent(intent) {
      // Exit first (restores focus), then run the caller's choice handler. Order is deliberate: the
      // caller's callback may itself enter another mode or move focus, which must happen *after* this
      // mode has popped and restored, not be undone by the restore.
      modes.getState().exit(id);
      options.onChoose(intent);
    },
    render: () => (
      <ConfirmDialog
        message={options.message}
        confirmLabel={confirmLabel}
        dismissLabel={dismissLabel}
      />
    ),
  };
}

/** The dialog's presentation — a pure function of its props (rule 1), no store/bus knowledge. The
 * {@link Overlay} centers this box (its declared `presentation: 'modal'`); the box draws its own
 * border and hint line. C12 copies this shape for its real dialogs. */
function ConfirmDialog({
  message,
  confirmLabel,
  dismissLabel,
}: {
  readonly message: string;
  readonly confirmLabel: string;
  readonly dismissLabel: string;
}): JSX.Element {
  const theme = useTheme();
  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor={theme.warning}
      paddingX={2}
      paddingY={1}
    >
      <Text bold>{message}</Text>
      <Text dimColor>{`y: ${confirmLabel}   n/esc: ${dismissLabel}`}</Text>
    </Box>
  );
}
