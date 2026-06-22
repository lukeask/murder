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

import { type Key, useInput, useStdin } from 'ink';
import { useEffect, useRef } from 'react';
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
  isStagePaneId,
  mountedStagePanesOf,
  resolveFocus,
  type StagePaneId,
} from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelStoreApi } from '../input/panelStore.js';
import type { PanelId } from '../input/panels.js';
import { toastStore } from '../store/toast/toastStore.js';
import type { Wheel } from '../terminal/StdinShim.js';
import type { Chord } from '../terminal/translate.js';
import { useInputStores } from './useInputStores.js';

/** Lines a single wheel notch scrolls. 3 is the conventional terminal step (xterm's default), and
 * brisker than the `j`/`k` single-line step so a flick of the wheel moves a meaningful chunk. */
const WHEEL_STEP = 3;

/** Handlers for the global chords owned by *later* chunks, injected so this hook stays complete now
 * without stubbing their behaviour. Both default to safe behaviour described on {@link useRootInput}. */
export interface DeferredGlobalHandlers {
  /** `alt+s`. Default: focus chat (the text input that becomes the spawn wizard, C13). */
  spawn?: () => void;
  /** `alt+y`. Default: no-op until C14 wires the tmux toggle. */
  toggleTmux?: () => void;
  /** `alt+t` / `ctrl+t` (TUIchat-3): cycle the focused chat pane's view mode. Default: no-op until the
   * shell wires the focus→agentId resolution + `cyclePaneViewMode` action. */
  cycleChatView?: () => void;
  /** `alt+p`. Default: no-op until C12 wires the new-plan dialog. */
  newPlan?: () => void;
  /** `alt+t`. Default: no-op until C12 wires the new-ticket dialog. */
  newTicket?: () => void;
  /** `alt+o` / `ctrl+o` (the `global.settings` action). Default: no-op until a later phase wires the settings
   * modal. Present now so the action's handler slot exists end-to-end (the registry already declares
   * the chord); the dispatcher does not yet route to it — it is wired when the modal lands. */
  openSettings?: () => void;
  /** `ctrl+n` (the `global.quickNote` action). Default: no-op until the shell wires the note-capture
   * modal. The chord is routed in the dispatcher ahead of the command-modifier gate, so this slot is
   * live; the shell supplies the real handler. */
  quickNote?: () => void;
  /** `?` (the `global.keyHelp` action). Default: no-op until the shell supplies the help-overlay
   * handler. The dispatcher already routes the chord (gated to non-chat focus). */
  keyHelp?: () => void;
  /** `alt+h`/`ctrl+h` (`global.cycleTargetPrev`). Default: no-op until the shell wires chat-target
   * cycling. Fires only while chat is focused (item 9 super-chords). */
  cycleTargetPrev?: () => void;
  /** `alt+l`/`ctrl+l` (`global.cycleTargetNext`). Default: no-op (see cycleTargetPrev). */
  cycleTargetNext?: () => void;
  /** `alt+w`/`ctrl+w` (`global.toggleTargetPane`). Default: no-op until the shell wires the pane
   * toggle. Fires only while chat is focused (item 9 super-chords). */
  toggleTargetPane?: () => void;
  /** `ctrl+m` (`global.murder`): arm the murder confirm for the targeted crow. Default: no-op until
   * the shell supplies the handler (the dispatcher already routes the chord + the pending check). */
  murder?: () => void;
  /** Whether a murder confirm is armed. Default: `false` (the pending check is inert), so chunks/
   * tests that don't wire murder see zero behavior change. */
  murderPending?: () => boolean;
  /** The confirm press (`m`/ctrl+m while armed). Default: no-op. */
  murderConfirm?: () => void;
  /** Any other key while armed cancels (without consuming the event). Default: no-op. */
  murderCancel?: () => void;
  /** `ctrl+q` (`global.closePane`): close the highlighted Stage pane. Default: no-op until the shell
   * supplies the handler (it needs the app store's docView / conversations actions, which this hook
   * does not hold). The dispatcher already routes the chord (gated to Stage-pane focus). */
  closePane?: () => void;
  /**
   * The persistent chat-input handler (C11, part F). Supplied by the shell (it needs both the chat
   * buffer store and the send action). When absent, layer 2 declines as before — so older
   * chunks/tests are unaffected. See {@link ChatInputHandler} and the dispatcher's layer 2.
   */
  chatInput?: ChatInputHandler;
  /**
   * Resolve the agent whose chat-history pane the mouse wheel should scroll while the chat INPUT
   * holds focus — i.e. the input's active send target. Returns the agentId, or `null` when there is
   * no target. Supplied by the shell (it reads conversations/roster/favorites); the wheel only acts
   * if that agent's pane is currently shown on the Stage (checked here against the rects map). Absent
   * → the chat-input wheel case is a no-op.
   */
  chatScrollTargetAgentId?: () => string | null;
}

/**
 * The terminal-side chord source — the {@link ../terminal/StdinShim.js StdinShim} emits a `chord`
 * event for command combos that have no legacy byte representation (ctrl+digit/space/i/m/h). The root
 * input loop subscribes here so those chords flow into the very same dispatch path as Ink key events.
 * Modelled as a minimal `on`/`off` pair (an `EventEmitter` subset) so this hook needs no Ink/stream
 * type and a test can drive it with a plain emitter. Optional: in bypass mode (modifier=alt) the shim
 * emits nothing, and a smoke/test run may omit it entirely.
 */
export interface TerminalEvents {
  on(event: 'chord', listener: (chord: Chord) => void): unknown;
  off(event: 'chord', listener: (chord: Chord) => void): unknown;
  /** Mouse-wheel notches lifted from SGR mouse reports (the shim emits these when mouse reporting is
   * enabled). Routed by effective focus to the focused/targeted pane's scroll. */
  on(event: 'wheel', listener: (wheel: Wheel) => void): unknown;
  off(event: 'wheel', listener: (wheel: Wheel) => void): unknown;
}

/**
 * Build a full Ink {@link Key} from a side-channel {@link Chord}. The chord carries only the base key
 * + ctrl/alt/shift; everything else is false. The special-key collision names (`tab`/`return`/
 * `backspace`, from ctrl+i/m/h) map onto the corresponding Ink flags with an empty `input`, exactly as
 * Ink reports those keys — so a binding can match them by flag. Every other chord keeps its printable
 * `input` char. Exported for the dispatch-path test (raw kitty bytes → chord → key → intent).
 */
export function chordToKey(chord: Chord): { input: string; key: Key } {
  const base: Key = {
    upArrow: false,
    downArrow: false,
    leftArrow: false,
    rightArrow: false,
    pageDown: false,
    pageUp: false,
    home: false,
    end: false,
    return: false,
    escape: false,
    ctrl: chord.ctrl,
    shift: chord.shift,
    tab: false,
    backspace: false,
    delete: false,
    meta: chord.alt,
    super: false,
    hyper: false,
    capsLock: false,
    numLock: false,
  };
  switch (chord.input) {
    case 'tab':
      return { input: '', key: { ...base, tab: true } };
    case 'return':
      return { input: '', key: { ...base, return: true } };
    case 'backspace':
      return { input: '', key: { ...base, backspace: true } };
    default:
      return { input: chord.input, key: base };
  }
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
export function useRootInput(
  deferred: DeferredGlobalHandlers = {},
  terminalEvents?: TerminalEvents,
): void {
  const { panels, focus, keymaps, modes, bindings, paneScroll } = useInputStores();
  // Only claim raw mode when the terminal supports it (see the raw-mode note above).
  const { isRawModeSupported } = useStdin();

  // The single per-event decision, shared by the Ink `useInput` loop and the terminal chord channel.
  // Both feed `(input, key)` here so a side-channel chord (ctrl+1, …) takes the exact same layered
  // dispatch path as a native key event — there is one dispatch policy, two entry points.
  const handleKey = (input: string, key: Key): void => {
    {
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
              // Scope the frame stream to the focused chat pane's crow: the raw view is the
              // parsing backup for the conversation under the cursor. A non-chat focus (roster,
              // a panel) yields no agent — the mode then streams the service's own session.
              const effective = resolveFocus(
                focusState.intendedId,
                panels.getState().visible,
                mountedStagePanesOf(focusState.rects),
              );
              const agentId = effective.startsWith('stage:chat:')
                ? effective.slice('stage:chat:'.length)
                : undefined;
              if (agentId === undefined) {
                // No focused chat → there's no crow session to mirror. Entering the mode here would
                // stream the service's own (nonexistent) session and show a raw "can't find pane"
                // error. Nudge the user to focus a chat first instead.
                toastStore
                  .getState()
                  .push("focus a crow's chat to mirror it (C-hjkl into the Stage)", {
                    ttlMs: 8000,
                  });
                return;
              }
              modesState.enter(tmuxMode(modes, agentId));
            }
          }),
        // TUIchat-3: cycle the focused chat pane's view mode. Default no-op until the shell wires it
        // (App supplies the focus→agentId resolution + the cyclePaneViewMode action call).
        cycleChatView: deferred.cycleChatView ?? (() => {}),
        // C12: newPlan / newTicket default to no-ops until the caller supplies real handlers.
        newPlan: deferred.newPlan ?? (() => {}),
        newTicket: deferred.newTicket ?? (() => {}),
        // Phase 5: openSettings defaults to a no-op until the shell supplies the settings-modal
        // handler. The `global.settings` chord is now routed in the dispatcher, so this slot is live.
        openSettings: deferred.openSettings ?? (() => {}),
        // ctrl+n: open the quick-note capture. Default no-op until the shell wires it.
        quickNote: deferred.quickNote ?? (() => {}),
        // Item 12 / item 9 super-chords: help overlay + chat-target cycling/toggle. Default to no-ops
        // until the shell supplies handlers; the dispatcher already routes the chords.
        keyHelp: deferred.keyHelp ?? (() => {}),
        cycleTargetPrev: deferred.cycleTargetPrev ?? (() => {}),
        cycleTargetNext: deferred.cycleTargetNext ?? (() => {}),
        toggleTargetPane: deferred.toggleTargetPane ?? (() => {}),
        // ctrl+m murder chord: arm/confirm/cancel + the pending query. Defaults keep the pending
        // check inert (`false`) so unwired chunks/tests see zero behavior change.
        murder: deferred.murder ?? (() => {}),
        murderPending: deferred.murderPending ?? (() => false),
        murderConfirm: deferred.murderConfirm ?? (() => {}),
        murderCancel: deferred.murderCancel ?? (() => {}),
        // ctrl+q close-pane: default no-op until the shell wires the docView/conversations actions.
        // The dispatcher only fires this when a Stage pane holds focus.
        closePane: deferred.closePane ?? (() => {}),
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
    }
  };

  // Mouse-wheel scroll routing. Focus-based, not pointer-based (the design the user chose): the wheel
  // scrolls whatever pane the highlight is on. A focused Stage pane (chat history or doc) scrolls
  // itself; with the chat INPUT focused the wheel scrolls the input's active send-target history pane
  // IF that pane is currently shown, else it is a no-op (no off-screen scrolling). A focused panel/
  // modal does not scroll (no Stage pane to drive). The pane applies the delta to its own local
  // offset via the scroll bus, clamped to its own range (only the pane knows its content length).
  const handleWheel = (wheel: Wheel): void => {
    const focusState = focus.getState();
    const effective = resolveFocus(
      focusState.intendedId,
      panels.getState().visible,
      mountedStagePanesOf(focusState.rects),
    );
    let target: StagePaneId | null = null;
    if (isStagePaneId(effective)) {
      // A focused chat-history or doc pane scrolls itself.
      target = effective;
    } else if (effective === CHAT_FOCUS) {
      // Typing in the chat input: scroll the active target's history pane, but only if it's on-screen.
      const agentId = deferred.chatScrollTargetAgentId?.() ?? null;
      if (agentId !== null) {
        const candidate: StagePaneId = `stage:chat:${agentId}`;
        if (focusState.rects.has(candidate)) {
          target = candidate;
        }
      }
    }
    if (target !== null) {
      paneScroll.emit(target, wheel.direction, WHEEL_STEP);
    }
  };

  useInput(
    (input, key) => {
      handleKey(input, key);
    },
    // `=== true` so an `undefined` (non-TTY stdin) is a hard `false`: `useInput` skips raw mode only
    // on a strict `isActive === false`, so a falsy-but-not-false value would still try to claim it.
    { isActive: isRawModeSupported === true },
  );

  // Keep the latest `handleKey` in a ref so the chord subscription is installed once per terminal
  // source (not re-subscribed every render) yet always calls the current closure — no stale state.
  // `handleKey` closes only over stable store handles + `deferred` (whose handlers read state at call
  // time), so this is belt-and-braces, but it also keeps the effect's dependency list honest.
  const handleKeyRef = useRef(handleKey);
  handleKeyRef.current = handleKey;
  const handleWheelRef = useRef(handleWheel);
  handleWheelRef.current = handleWheel;

  // The terminal chord side-channel (Phase 2). When the kitty shim is wired and the protocol is
  // active, it emits `chord` events for command combos with no legacy byte encoding (ctrl+digit, …);
  // each is lifted to an Ink `(input, key)` and dispatched identically. Re-subscribe only when the
  // source identity changes (stable for a run). In bypass mode the shim emits nothing, so this is
  // inert under the alt default.
  useEffect(() => {
    if (terminalEvents === undefined) {
      return;
    }
    const onChord = (chord: Chord): void => {
      const { input, key } = chordToKey(chord);
      handleKeyRef.current(input, key);
    };
    const onWheel = (wheel: Wheel): void => {
      handleWheelRef.current(wheel);
    };
    terminalEvents.on('chord', onChord);
    terminalEvents.on('wheel', onWheel);
    return () => {
      terminalEvents.off('chord', onChord);
      terminalEvents.off('wheel', onWheel);
    };
  }, [terminalEvents]);
}
