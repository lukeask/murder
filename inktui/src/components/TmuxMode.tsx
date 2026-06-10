/**
 * TmuxMode — the C14 full-screen mode that streams raw tmux ANSI frames into the TUI.
 *
 * `ctrl+y` toggles the mode from any view: entering opens the tmux-frame subscription and renders
 * the focused pane as a live ANSI frame; exiting (`ctrl+y` again, or Escape) closes the subscription
 * and restores the prior focus via C7M. No subscription is open while the mode is inactive.
 *
 * ## How this fits the C7M recipe (copy from ConfirmModal.tsx):
 *
 *  1. **Declare a mode as data** — {@link tmuxMode} builds a {@link Mode}: `id`, `presentation:
 *     'fullscreen'` (the shell hides bars/panels via `presentationHidesLayout` — no App.tsx edit
 *     needed), keymap with `ctrl+y`/`Escape` as dismiss chords, `onIntent → exit(id)`, and a thin
 *     `render: () => <TmuxFrame/>`. `passThrough: true` lets `ctrl+y` fall through from layer 1 so
 *     the global `toggleTmux` handler can see it (otherwise layer 0 swallows it before the chord
 *     layer fires, breaking the "ctrl+y again to exit" path).
 *  2. **Enter it** — `toggleTmux` in `useRootInput.ts` calls `modes.getState().enter(tmuxMode(...))`,
 *     but only when the mode is not already active; when it is active, `toggleTmux` calls
 *     `modes.getState().exit(TMUX_MODE_ID)`. Together this gives the toggle behaviour.
 *  3. **Subscription lifecycle** — {@link TmuxFrame} opens the bus subscription inside `useEffect`
 *     and closes it in the cleanup (the `useEffect` disposer). Because React unmounts `TmuxFrame`
 *     whenever `exit()` is called (the Overlay stops rendering it), the cleanup fires for *every*
 *     exit path — `ctrl+y` again, Escape, or `exit(id)` from anywhere — with no leaked handles.
 *     This is the leak-free proof: the subscription is tied to mount/unmount, not to a handler.
 *  4. **ANSI render** — Ink `<Text>` renders ANSI escape sequences natively, so the latest frame
 *     string is passed directly. Local `useState` holds the latest frame; each `tmux.frame` event
 *     fires the setState, causing a re-render.
 *
 * ## Why `passThrough: true`
 *
 * The dispatcher's layer 0 captures every key and swallows non-matches by default. Without
 * `passThrough`, `ctrl+y` would be swallowed before reaching layer 1 (the global-chord layer), so
 * `toggleTmux` could never see it and "ctrl+y again to exit" would silently not work. With
 * `passThrough: true`, a key the mode's keymap does not match falls through to layer 1. We still
 * declare Escape as a first-class dismiss chord so dismissal via Escape is also handled cleanly.
 *
 * ## Pane-scoping
 * The current {@link EventFilter} has no `pane_id` field.  The service delivers a single stream
 * for its configured focused pane; per-pane multiplexing is deferred (C14 follow-up).
 */

import { Box, Text } from 'ink';
import type { JSX } from 'react';
import { useEffect, useState } from 'react';
import type { TmuxFrameEvent } from '../bus/protocol.js';
import { useBusClient } from '../hooks/useBusClient.js';
import type { Mode, ModeStoreApi } from '../input/modeStore.js';

/** Stable id for the tmux fullscreen mode. Used in `useRootInput` to detect if the mode is already
 * active (toggle-off path). Exported so the hook wiring can reference the same constant. */
export const TMUX_MODE_ID = 'tmux-fullscreen';

/** The tmux mode's intent union — its own action names, so `onIntent` is exhaustively typed. */
type TmuxIntent = 'dismiss';

/**
 * Build the tmux fullscreen {@link Mode}. Pass the `modes` store handle so the mode can `exit`
 * itself when a dismiss key is pressed — canonical self-dismissing mode shape (same as
 * {@link ConfirmModal.confirmMode}).
 *
 * `passThrough: true` so `ctrl+y` (which the mode's keymap does NOT declare) falls through to
 * layer 1 and fires the global `toggleTmux` handler (which calls `exit` when the mode is active).
 * Escape is declared as an explicit dismiss chord for keyboard-friendly dismissal without needing
 * the toggle.
 */
export function tmuxMode(modes: ModeStoreApi): Mode<TmuxIntent> {
  return {
    id: TMUX_MODE_ID,
    presentation: 'fullscreen',
    // passThrough = true: ctrl+y not declared here, so it falls through to the global chord layer
    // where toggleTmux exits the mode. Without this, layer 0 would swallow ctrl+y.
    passThrough: true,
    keymap: [
      { chord: { key: { escape: true } }, intent: 'dismiss', description: 'exit tmux view' },
    ],
    onIntent(intent) {
      // Exhaustive over TmuxIntent. Exit (restores prior focus via C7M), then nothing else needed.
      switch (intent) {
        case 'dismiss':
          modes.getState().exit(TMUX_MODE_ID);
          return;
        default:
          return intent satisfies never;
      }
    },
    render: () => <TmuxFrame />,
  };
}

/** Fallback text before the first frame arrives (or if the service stream hasn't started yet). */
const WAITING_TEXT = '[waiting for tmux frame…]';

/**
 * The full-screen tmux frame renderer. Opens the `tmux.frame` bus subscription on mount; renders
 * the latest ANSI frame string (or a waiting placeholder); closes the subscription on unmount.
 *
 * Subscription lifecycle is entirely `useEffect`-managed: every exit path (ctrl+y, Escape, or
 * any other code calling `exit(TMUX_MODE_ID)`) causes the Overlay to stop rendering this component,
 * which triggers the effect cleanup. No leaked handles are possible — the cleanup is the proof.
 *
 * Rule 1 note: this component does call `useBusClient()` (for the subscription), which is a narrow
 * exception granted for transient streaming data (not a domain slice). See the `useBusClient` module
 * doc for the reasoning. The component is still a pure renderer of its local `frame` state (the bus
 * subscription is a side-effect that updates that state, not a store query).
 */
function TmuxFrame(): JSX.Element {
  const bus = useBusClient();
  const [frame, setFrame] = useState<string>('');

  useEffect(() => {
    // Open the tmux frame subscription filtered to the frame event type. The subscription is opened
    // here (on mount) and closed in the cleanup (on unmount). This is the only place it is managed.
    const unsubscribe = bus.subscribe(
      (event) => {
        if (event.type !== 'tmux.frame') {
          return;
        }
        const tmuxEvent: TmuxFrameEvent = event;
        setFrame(tmuxEvent.frame);
      },
      { type: 'tmux.frame' },
    );
    // Cleanup: close the subscription when the component unmounts (i.e. when the mode exits,
    // whether via ctrl+y, Escape, or any other path). This is the leak-free guarantee.
    return unsubscribe;
  }, [bus]);

  return (
    <Box flexDirection="column" width="100%" height="100%">
      <Text>{frame !== '' ? frame : WAITING_TEXT}</Text>
    </Box>
  );
}
