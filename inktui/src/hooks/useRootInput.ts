/**
 * `useRootInput` — the single `useInput` call for the whole app. Mount it once at the app shell; it
 * is the only place Ink key events are read (rule 5). It gathers the live focus/panel/keymap state
 * and the global-intent handlers, then hands each event to the pure {@link dispatchKey} for the
 * layered decision. No component below this calls `useInput`.
 *
 * Global-intent wiring lives here because it is where the input stores meet:
 *  - `focusPanel(id)` = **show the panel, then focus it** — the plan's "`ctrl+<n>` brings highlight
 *    to that component, toggling it on if off". Show-then-focus (not toggle) so the chord is "go
 *    there", never "hide the thing I'm trying to reach". (A second `ctrl+<n>` to *hide* is a later
 *    UX call; the primitive `panelStore.toggle` is there if a chunk wants it.)
 *  - `navigate(dir)` delegates to the focus store's geometry-driven `navigate`.
 *  - `focusChat()` points focus at chat (`ctrl+f`).
 *  - `spawn()` / `toggleTmux()` are owned by later chunks (C13 spawn wizard, C14 tmux); they are
 *    injectable so those chunks supply real handlers, defaulting to safe no-ops (spawn defaults to
 *    focusing chat, matching the plan's "`ctrl+s` → highlight to text input").
 *
 * Raw-mode guard: `useInput` puts stdin in raw mode, which a non-TTY stdin (a piped `npm run dev`
 * smoke run, CI, a `< /dev/null` invocation) does not support — Ink throws if asked. So the loop is
 * `isActive` only when {@link useStdin} reports `isRawModeSupported`. The handler is still installed
 * (hook order is stable); it just doesn't claim raw mode when there's no interactive terminal. Under
 * `ink-testing-library` raw mode *is* supported, so tests drive the live loop unchanged.
 */

import { useInput, useStdin } from 'ink';
import { TMUX_MODE_ID, tmuxMode } from '../components/TmuxMode.js';
import { type DispatchContext, dispatchKey, type GlobalHandlers } from '../input/dispatcher.js';
import { CHAT_FOCUS, resolveFocus } from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import { useInputStores } from './useInputStores.js';

/** Handlers for the global chords owned by *later* chunks, injected so this hook stays complete now
 * without stubbing their behaviour. Both default to safe behaviour described on {@link useRootInput}. */
export interface DeferredGlobalHandlers {
  /** `ctrl+s`. Default: focus chat (the text input that becomes the spawn wizard, C13). */
  spawn?: () => void;
  /** `ctrl+y`. Default: no-op until C14 wires the tmux toggle. */
  toggleTmux?: () => void;
  /** `ctrl+p`. Default: no-op until C12 wires the new-plan dialog. */
  newPlan?: () => void;
  /** `ctrl+t`. Default: no-op until C12 wires the new-ticket dialog. */
  newTicket?: () => void;
}

/**
 * Install the root input loop. Reads the input stores from context, builds the dispatch context per
 * event (so it always sees current focus + registered keymaps), and routes via {@link dispatchKey}.
 * Returns nothing — it is an effect-like hook; call it once in the shell.
 */
export function useRootInput(deferred: DeferredGlobalHandlers = {}): void {
  const { panels, focus, keymaps, modes } = useInputStores();
  // Only claim raw mode when the terminal supports it (see the raw-mode note above).
  const { isRawModeSupported } = useStdin();

  useInput(
    (input, key) => {
      const focusState = focus.getState();
      const handlers: GlobalHandlers = {
        focusPanel(id) {
          panels.getState().show(id);
          focusState.focus(id);
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
            // `passThrough: true` on the mode lets ctrl+y fall through from layer 0 to layer 1
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
        focusedId: resolveFocus(focusState.intendedId, panels.getState().visible),
        panelKeymaps: keymaps.getState().keymaps,
        handlers,
        // Layer 0: the live active mode (stack top), or null. Read per-event so a mode entered/exited
        // mid-session takes effect on the very next key without re-installing the loop.
        activeMode: selectActiveMode(modes),
      };

      dispatchKey(input, key, ctx);
    },
    // `=== true` so an `undefined` (non-TTY stdin) is a hard `false`: `useInput` skips raw mode only
    // on a strict `isActive === false`, so a falsy-but-not-false value would still try to claim it.
    { isActive: isRawModeSupported === true },
  );
}
