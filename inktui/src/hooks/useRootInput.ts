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
 *  - `spawn()` is owned by a later chunk (C13 spawn wizard); it is injectable so that chunk supplies
 *    the real handler, defaulting to a safe no-op (spawn defaults to focusing chat, matching the
 *    plan's "`alt+s` → highlight to text input"). (The old `toggleTmux()` fullscreen-tmux handler was
 *    retired in TUIchat-5; tmux is now an inline per-pane view in the transcript pane.)
 *
 * Raw-mode guard: `useInput` puts stdin in raw mode, which a non-TTY stdin (a piped `npm run dev`
 * smoke run, CI, a `< /dev/null` invocation) does not support — Ink throws if asked. So the loop is
 * `isActive` only when {@link useStdin} reports `isRawModeSupported`. The handler is still installed
 * (hook order is stable); it just doesn't claim raw mode when there's no interactive terminal. Under
 * `ink-testing-library` raw mode *is* supported, so tests drive the live loop unchanged.
 */

import { type Key, useInput, useStdin } from 'ink';
import { useEffect, useRef } from 'react';
import {
  type ChatInputHandler,
  type DispatchContext,
  dispatchKey,
  type GlobalHandlers,
} from '../input/dispatcher.js';
import {
  CHAT_FOCUS,
  type FocusStoreApi,
  type StagePaneId,
  selectEffectiveFocus,
  selectResolvedFocus,
  stageTranscriptFocusId,
} from '../input/focusStore.js';
import { selectActiveMode } from '../input/modeStore.js';
import type { PanelStoreApi } from '../input/panelStore.js';
import type { PanelId } from '../input/panels.js';
import { keyUsageStore } from '../store/keyUsage/keyUsageStore.js';
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
  /** `alt+t` / `ctrl+t` (TUIchat-3): cycle the focused transcript pane's view mode. Default: no-op until the
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
  /** `alt+h`/`ctrl+h` (`global.cycleTargetPrev`). Default: no-op until the shell wires recipient-target
   * cycling. Fires only while chat is focused (item 9 super-chords). */
  cycleTargetPrev?: () => void;
  /** `alt+l`/`ctrl+l` (`global.cycleTargetNext`). Default: no-op (see cycleTargetPrev). */
  cycleTargetNext?: () => void;
  /** `ctrl+j` (`global.toggleTargetGroup`). Default: no-op until the shell wires group toggle. */
  toggleTargetGroup?: () => void;
  /** `alt+w`/`ctrl+w` (`global.toggleTargetPane`). Default: no-op until the shell wires the pane
   * toggle. Fires while chat or a Stage pane is focused. */
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
  /** `ctrl+r` (`global.repaint`): force a full terminal redraw. Default: no-op until the shell wires
   * {@link forceInkFullRepaint}. ctrl+l is taken by target cycling. */
  repaint?: () => void;
  /** `<Cmd>+Shift+J` (`workspace.next`). Default: no-op until the shell wires workspace switching. */
  workspaceNext?: () => void;
  /** `<Cmd>+Shift+K` (`workspace.prev`). Default: no-op until the shell wires workspace switching. */
  workspacePrev?: () => void;
  /** `<Cmd>+Shift+<n>` (`workspace.jump.<n>`). Default: no-op until the shell wires workspace switching. */
  workspaceJump?: (index: number) => void;
  /**
   * The persistent chat-input handler (C11, part F). Supplied by the shell (it needs both the chat
   * buffer store and the send action). When absent, layer 2 declines as before — so older
   * chunks/tests are unaffected. See {@link ChatInputHandler} and the dispatcher's layer 2.
   */
  chatInput?: ChatInputHandler;
  /**
   * Resolve the agent whose transcript pane the mouse wheel should scroll while the chat INPUT
   * holds focus — i.e. the input's active send target. Returns the agentId, or `null` when there is
   * no target. Supplied by the shell (it reads conversations/roster/favorites); the wheel only acts
   * if that agent's pane is currently shown in the center-stage group. Absent → the chat-input wheel
   * case is a no-op.
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
  /** Mouse-wheel notches lifted from SGR mouse reports (the shim emits these when mouse reporting is
   * enabled). Routed by effective focus to the focused/targeted pane's scroll. */
  on(event: 'wheel', listener: (wheel: Wheel) => void): unknown;
  off(event: 'chord', listener: (chord: Chord) => void): unknown;
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

function normalizeTerminalKey(input: string, key: Key): { input: string; key: Key } {
  if (key.backspace === true) {
    return { input, key };
  }
  // Raw-mode Backspace is not universal: local terminals commonly send DEL (0x7f), while SSH/tmux
  // paths can deliver BS (0x08). If Ink surfaces either byte as input instead of key.backspace,
  // normalize it before the dispatcher/mode keymaps see the event.
  if (input === '\x7f' || input === '\x08') {
    return { input: '', key: { ...key, backspace: true } };
  }
  return { input, key };
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
  const effectiveFocus = selectEffectiveFocus(focus);

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
  const { panels, focus, keymaps, modes, bindings, paneScroll, workspace } = useInputStores();
  // Only claim raw mode when the terminal supports it (see the raw-mode note above).
  const { isRawModeSupported } = useStdin();

  // The single per-event decision, shared by the Ink `useInput` loop and the terminal chord channel.
  // Both feed `(input, key)` here so a side-channel chord (ctrl+1, …) takes the exact same layered
  // dispatch path as a native key event — there is one dispatch policy, two entry points.
  const handleKey = (input: string, key: Key): void => {
    {
      // Workspace slide in flight: ALL input is swallowed (including further workspace keybinds) —
      // the transition lasts < half a second and the layout being animated is not the live one, so
      // routing a key anywhere would act on a view the user cannot see. Read per-event, like the
      // active mode.
      if (workspace.getState().transition !== null) {
        return;
      }
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
        // TUIchat-3: cycle the focused transcript pane's view mode. Default no-op until the shell wires it
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
        toggleTargetGroup: deferred.toggleTargetGroup ?? (() => {}),
        toggleTargetPane: deferred.toggleTargetPane ?? (() => {}),
        // ctrl+m murder chord: arm/confirm/cancel + the pending query. Defaults keep the pending
        // check inert (`false`) so unwired chunks/tests see zero behavior change.
        murder: deferred.murder ?? (() => {}),
        murderPending: deferred.murderPending ?? (() => false),
        murderConfirm: deferred.murderConfirm ?? (() => {}),
        murderCancel: deferred.murderCancel ?? (() => {}),
        // ctrl+r redraw: default no-op until the shell wires forceInkFullRepaint.
        repaint: deferred.repaint ?? (() => {}),
        workspaceNext: deferred.workspaceNext ?? (() => {}),
        workspacePrev: deferred.workspacePrev ?? (() => {}),
        workspaceJump: deferred.workspaceJump ?? (() => {}),
      };

      const ctx: DispatchContext = {
        // Effective focus, so layer 2/3 route against where the highlight actually is after graph
        // re-home. A mounted center-stage pane resolves to itself; an unmounted pane resolves to chat.
        focusedId: selectResolvedFocus(focus).id,
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

      const outcome = dispatchKey(input, key, ctx);
      if (outcome.action !== undefined) {
        keyUsageStore.getState().recordUse(outcome.action);
      }
    }
  };

  // Mouse-wheel scroll routing. Focus-based, not pointer-based (the design the user chose): the wheel
  // scrolls whatever pane the highlight is on. A focused center-stage pane (transcript or doc) scrolls
  // itself; with the chat INPUT focused the wheel scrolls the input's active send-target history pane
  // IF that pane is currently shown, else it is a no-op (no off-screen scrolling). A focused panel/
  // modal does not scroll (no center-stage pane to drive). The pane applies the delta to its own local
  // offset via the scroll bus, clamped to its own range (only the pane knows its content length).
  const handleWheel = (wheel: Wheel): void => {
    // Same workspace-slide input block as handleKey: no scrolling a view that isn't on screen.
    if (workspace.getState().transition !== null) {
      return;
    }
    const focusState = focus.getState();
    const resolved = selectResolvedFocus(focus);
    let target: StagePaneId | null = null;
    if (resolved.target.kind === 'transcriptPane' || resolved.target.kind === 'docPane') {
      target = resolved.id as StagePaneId;
    } else if (resolved.target.kind === 'composer') {
      // Typing in the chat input: scroll the active target's transcript pane, but only if it's on-screen.
      const agentId = deferred.chatScrollTargetAgentId?.() ?? null;
      if (agentId !== null) {
        const candidate: StagePaneId = stageTranscriptFocusId(agentId);
        const paneIsAllocated =
          focusState.paneGeometries?.some((geometry) => geometry.id === candidate) ??
          focusState.rects.has(candidate);
        if (paneIsAllocated) {
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
      const normalized = normalizeTerminalKey(input, key);
      handleKey(normalized.input, normalized.key);
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
