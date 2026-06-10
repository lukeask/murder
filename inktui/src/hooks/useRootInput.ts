/**
 * `useRootInput` — the single `useInput` call for the whole app. Mount it once at the app shell; it
 * is the only place Ink key events are read (rule 5). It gathers the live focus/panel/keymap state
 * and the global-intent handlers, then hands each event to the pure {@link dispatchKey} for the
 * layered decision. No component below this calls `useInput`.
 *
 * Global-intent wiring lives here because it is where the input stores meet:
 *  - `focusPanel(id)` = panel shortcut semantics: show+focus when hidden; hide when visible. Hiding
 *    the focused panel explicitly returns focus intent to chat; hiding another panel leaves focus
 *    where it was.
 *  - `navigate(dir)` delegates to the focus store's geometry-driven `navigate`.
 *  - `focusChat()` points focus at chat (`alt+space`).
 *  - `spawn()` / `toggleTmux()` are owned by later chunks (C13 spawn wizard, C14 tmux); they are
 *    injectable so those chunks supply real handlers, defaulting to safe no-ops (spawn defaults to
 *    focusing chat, matching the plan's "`alt+s` → highlight to text input").
 *
 * Raw-mode guard: `useInput` puts stdin in raw mode, which a non-TTY stdin (a piped `npm run dev`
 * smoke run, CI, a `< /dev/null` invocation) does not support — Ink throws if asked. So the loop is
 * `isActive` only when {@link useStdin} reports `isRawModeSupported`. The handler is still installed
 * (hook order is stable); it just doesn't claim raw mode when there's no interactive terminal. Under
 * `ink-testing-library` raw mode *is* supported, so tests drive the live loop unchanged.
 */

import { useInput, useStdin } from 'ink';
import { TMUX_MODE_ID, tmuxMode } from '../components/TmuxMode.js';
import {
  type ChatInputHandler,
  type DispatchContext,
  dispatchKey,
  type GlobalHandlers,
} from '../input/dispatcher.js';
import {
  CHAT_FOCUS,
  type FocusStoreApi,
  mountedStagePanesOf,
  resolveFocus,
} from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelStoreApi } from '../input/panelStore.js';
import type { PanelId } from '../input/panels.js';
import { useInputStores } from './useInputStores.js';

/** Handlers for the global chords owned by *later* chunks, injected so this hook stays complete now
 * without stubbing their behaviour. Both default to safe behaviour described on {@link useRootInput}. */
export interface DeferredGlobalHandlers {
  /** `alt+s`. Default: focus chat (the text input that becomes the spawn wizard, C13). */
  spawn?: () => void;
  /** `alt+y`. Default: no-op until C14 wires the tmux toggle. */
  toggleTmux?: () => void;
  /** `alt+p`. Default: no-op until C12 wires the new-plan dialog. */
  newPlan?: () => void;
  /** `alt+t`. Default: no-op until C12 wires the new-ticket dialog. */
  newTicket?: () => void;
  /** `alt+,` (the `global.settings` action). Default: no-op until a later phase wires the settings
   * modal. Present now so the action's handler slot exists end-to-end (the registry already declares
   * the chord); the dispatcher does not yet route to it — it is wired when the modal lands. */
  openSettings?: () => void;
  /**
   * The persistent chat-input handler (C11, part F). Supplied by the shell (it needs both the chat
   * buffer store and the send action). When absent, layer 2 declines as before — so older
   * chunks/tests are unaffected. See {@link ChatInputHandler} and the dispatcher's layer 2.
   */
  chatInput?: ChatInputHandler;
}

/**
 * Shared panel-shortcut behaviour. Visibility and focus meet here:
 *  - hidden panel: show it and focus it
 *  - visible panel while focused: hide it and focus chat
 *  - visible panel while something else is focused: hide it and leave focus intent unchanged
 */
export function togglePanelFromShortcut(
  id: PanelId,
  panels: PanelStoreApi,
  focus: FocusStoreApi,
): void {
  const panelState = panels.getState();
  const focusState = focus.getState();
  const visible = panelState.visible;
  const effectiveFocus = resolveFocus(
    focusState.intendedId,
    visible,
    mountedStagePanesOf(focusState.rects),
  );

  if (!visible.has(id)) {
    panelState.show(id);
    focusState.focus(id);
    return;
  }

  panelState.hide(id);
  if (effectiveFocus === id) {
    focusState.focus(CHAT_FOCUS);
  }
}

/**
 * Install the root input loop. Reads the input stores from context, builds the dispatch context per
 * event (so it always sees current focus + registered keymaps), and routes via {@link dispatchKey}.
 * Returns nothing — it is an effect-like hook; call it once in the shell.
 */
export function useRootInput(deferred: DeferredGlobalHandlers = {}): void {
  const { panels, focus, keymaps, modes, bindings } = useInputStores();
  // Only claim raw mode when the terminal supports it (see the raw-mode note above).
  const { isRawModeSupported } = useStdin();

  useInput(
    (input, key) => {
      const focusState = focus.getState();
      const handlers: GlobalHandlers = {
        focusPanel(id) {
          togglePanelFromShortcut(id, panels, focus);
        },
        navigate(direction) {
          focusState.navigate(direction);
        },
        focusChat() {
          focusState.focus(CHAT_FOCUS);
        },
        spawn: deferred.spawn ?? (() => focusState.focus(CHAT_FOCUS)),
        toggleTmux:
          deferred.toggleTmux ??
          (() => {
            // C14 wiring: toggle the tmux fullscreen mode. If the mode is already active, exit it
            // (restores prior focus via C7M). If not active, enter it (saves current focus).
            // `passThrough: true` on the mode lets alt+y fall through from layer 0 to layer 1
            // (the global-chord layer) so this handler fires on the "exit" press too.
            const modesState = modes.getState();
            if (selectActiveMode(modes)?.id === TMUX_MODE_ID) {
              modesState.exit(TMUX_MODE_ID);
            } else {
              modesState.enter(tmuxMode(modes));
            }
          }),
        // C12: newPlan / newTicket default to no-ops until the caller supplies real handlers.
        newPlan: deferred.newPlan ?? (() => {}),
        newTicket: deferred.newTicket ?? (() => {}),
      };

      const ctx: DispatchContext = {
        // Effective focus, so layer 2/3 route against where the highlight actually is (post re-home).
        // Stage panes are derived from the rects map (mountedStagePanesOf) so a focused chat pane
        // resolves to itself, not chat — otherwise layer 2 would short-circuit its keys to the input.
        focusedId: resolveFocus(
          focusState.intendedId,
          panels.getState().visible,
          mountedStagePanesOf(focusState.rects),
        ),
        panelKeymaps: keymaps.getState().keymaps,
        handlers,
        // The live resolved binding table (modifier + rebinds). Read per-event so a settings change
        // takes effect on the very next key without re-installing the loop.
        bindings: bindings.getState().resolved,
        // Layer 0: the live active mode (stack top), or null. Read per-event so a mode entered/exited
        // mid-session takes effect on the very next key without re-installing the loop.
        activeMode: selectActiveMode(modes),
        // Layer 2: the persistent chat-input handler (C11). Only set the key when supplied
        // (exactOptionalPropertyTypes — an explicit `undefined` would differ from absent).
        ...(deferred.chatInput !== undefined ? { chatInput: deferred.chatInput } : {}),
      };

      dispatchKey(input, key, ctx);
    },
    // `=== true` so an `undefined` (non-TTY stdin) is a hard `false`: `useInput` skips raw mode only
    // on a strict `isActive === false`, so a falsy-but-not-false value would still try to claim it.
    { isActive: isRawModeSupported === true },
  );
}
