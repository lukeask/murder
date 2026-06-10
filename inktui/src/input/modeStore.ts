/**
 * `modeStore` — the transient-input-mode stack: the primitive every modal-ish surface (popup
 * dialog, in-layout editor, full-screen tmux view) is built on. A *mode* is a UI surface rendered
 * over (or in place of) the normal panels that **captures input exclusively** until dismissed, then
 * **restores the focus that was active when it opened**. C4 gave panels a focus state machine; this
 * gives the app a *capture* state machine layered on top.
 *
 * ## Why this exists (and why once)
 *
 * C8 (in-layout ticket editor), C12 (popup dialogs), and C14 (full-screen tmux) each independently
 * need exactly this: capture-until-dismissed + focus-restore. Built three times across parallel
 * chunks it would breed three divergent modal patterns — the very rot the backbone phase prevents.
 * So it is one deep module behind a narrow interface: `enter(mode)` / `exit(id?)`, plus a *declared*
 * keymap (reusing {@link Keymap} from keymap.ts — modes declare keys exactly as panels do, rule 5).
 * A consumer never hand-rolls input capture or focus juggling; it declares a mode and calls `enter`.
 *
 * ## Mode = data
 *
 * An active mode is plain data (rule 4 — this store is framework-agnostic vanilla Zustand, no React
 * runtime import; `render` is carried as an opaque type-only `ReactNode` thunk the store never
 * invokes, so the store stays portable to a future DOM client that would supply a DOM render). The
 * mode carries:
 *  - `id` — identity, so `exit(id)` is unambiguous and a re-`enter` of the same id is idempotent.
 *  - `keymap` + `onIntent` — the {@link PanelKeymap} shape, so the dispatcher routes captured keys to
 *    the mode's *declared* chords (its dismiss key is just another declared chord whose intent the
 *    `onIntent` handles by calling `exit`). No special-cased Escape handling in the dispatcher.
 *  - `presentation` — how the {@link ../components/Overlay.js Overlay} paints it: centered `modal`,
 *    `fullscreen` takeover, or `inlayout` region. Presentation is *data*, so the three consumers pick
 *    a variant instead of each reinventing layout.
 *  - `passThrough` — opt-in: when `true`, the dispatcher, after offering a captured key to the mode's
 *    keymap and finding no match, falls through to the lower layers (global chords, panels). Default
 *    `false`: a mode captures *everything*, the safe modal default. C14's full-screen tmux may set it
 *    so `ctrl+<n>` still works underneath; C12's dialog leaves it off.
 *  - `render` — the thin component that draws the surface. The only React-shaped field, kept type-only.
 *
 * ## Stack semantics
 *
 * Modes stack: a mode opened over a mode pushes; `exit()` pops the top, `exit(id)` removes that id
 * wherever it sits. The *active* mode is the stack top — the dispatcher and overlay read only it.
 * Each stack frame records the effective focus that was live when it was pushed, so popping restores
 * exactly the focus the user came from (a dialog opened from the tickets panel returns there; a
 * dialog opened over another dialog returns to that dialog's focus, then to the panel).
 *
 * ## Focus save/restore — one managed transition, not scattered re-homing
 *
 * Unlike C4's panel re-home (which is *derived* — "focused on a hidden panel" is unrepresentable),
 * modal focus genuinely must be *saved and restored*: the prior focus is real state that outlives
 * the capture and has to come back. There is no derivation that reconstructs "where the user was"
 * after the fact. So this is an explicit save-on-push / restore-on-pop, but contained to this one
 * transition (the store reads + writes the focus store it is bound to) — never leaked to consumers
 * and never scattered. A consumer calls `enter`/`exit`; focus handling is the store's job, exactly as
 * C4 contained the re-home invariant in `focusStore`. (We do *not* try to make it derived: a derived
 * version would need to store the prior focus somewhere to derive from, which is just this save under
 * another name, with more indirection — the explicit save is the honest, narrower expression.)
 */

import type { ReactNode } from 'react';
import { createStore, type StoreApi } from 'zustand/vanilla';
import type { FocusId, FocusStoreApi } from './focusStore.js';
import { selectEffectiveFocus } from './focusStore.js';
import type { PanelKeymap } from './keymap.js';

/** How the overlay presents a mode. Data, not a branch the consumer hardcodes: the three surfaces
 * C8/C12/C14 need, each picking one. `modal` = centered box over the panels; `fullscreen` = replaces
 * the whole layout; `inlayout` = occupies a region while panels stay visible. */
export type ModePresentation = 'modal' | 'fullscreen' | 'inlayout';

/**
 * One active mode, as data. `Intent` is the mode's own action-name union (a string union), so its
 * `onIntent` is exhaustively typed against its own keymap — identical ergonomics to a panel keymap.
 */
export interface Mode<Intent extends string = string> extends PanelKeymap<Intent> {
  /** Stable identity. `exit(id)` targets it; re-`enter` of the same id replaces in place (idempotent
   * for the focus save — see {@link ModeState.enter}). */
  readonly id: string;
  /** How the overlay paints this mode. */
  readonly presentation: ModePresentation;
  /** When `true`, a captured key the mode's keymap does not match falls through to the lower
   * dispatch layers; default `false` (capture everything). See the module doc. */
  readonly passThrough?: boolean;
  /** Optional bottom-bar hints this mode contributes. When present, the bottom bar shows THESE
   * instead of the focused panel's keys for the duration of the mode (the mode captures input, so its
   * keys are the only relevant ones — e.g. the spawn wizard's `j/k nav · enter confirm · esc cancel`).
   * Each hint is `{ key, description }`; the bottom bar selector ({@link
   * ../selectors/barSelectors.js}) renders them like any other hint. */
  readonly hints?: readonly ModeHint[];
  /** The thin component that draws the surface. Carried opaquely — the store never calls it (the
   * {@link ../components/Overlay.js Overlay} does, at the React layer). Type-only React dependency so
   * the store itself stays framework-agnostic (rule 4). */
  readonly render: () => ReactNode;
}

/** A bottom-bar hint a mode contributes — the structural shape shared with {@link
 * ../selectors/barSelectors.js}'s `BottomBarHint` (kept structural here so the input layer doesn't
 * import the selectors). */
export interface ModeHint {
  readonly key: string;
  readonly description: string;
}

/** A pushed stack frame: the mode plus the effective focus that was live when it was pushed, so the
 * pop restores exactly there. The saved focus is internal bookkeeping — never exposed to consumers. */
interface ModeFrame {
  readonly mode: Mode;
  readonly savedFocus: FocusId;
}

/** The mode store's state: the stack and the verbs that push/pop it. */
export interface ModeState {
  /** The mode stack, bottom-to-top. The *active* mode is the last element (the top). Read-only to
   * callers; replaced wholesale on every push/pop so a `useStore` subscriber re-renders on change. */
  readonly stack: readonly ModeFrame[];
  /**
   * Enter a mode — push it as the new active mode and save the current effective focus so the
   * matching {@link exit} restores it. Re-entering an id already on the stack is idempotent: it is
   * removed and re-pushed to the top (so its saved focus is *not* clobbered to its own surface — we
   * preserve the original frame's saved focus), keeping a stable single instance per id.
   */
  enter<Intent extends string>(mode: Mode<Intent>): void;
  /**
   * Exit a mode and restore the focus saved when it was entered. `exit()` pops the top; `exit(id)`
   * removes that id wherever it sits. Restoring focus only happens for the *active* (top) frame's
   * removal — popping a buried frame leaves the live focus alone (the top mode still owns capture);
   * its saved focus is simply discarded. Exiting when the stack is empty, or an id not present, is a
   * no-op.
   */
  exit(id?: string): void;
}

/** The mode store handle, carrying the focus store it saves/restores against — so a reader needs
 * only this one handle, mirroring {@link FocusStoreApi} carrying its panel store. */
export type ModeStoreApi = StoreApi<ModeState> & { readonly focus: FocusStoreApi };

/** The currently active mode (the stack top), or `null` if no mode is up. The dispatcher's layer 0
 * and the overlay both read this — pure, safe in render. */
export function selectActiveMode(modes: ModeStoreApi): Mode | null {
  const { stack } = modes.getState();
  const top = stack[stack.length - 1];
  return top === undefined ? null : top.mode;
}

/**
 * Create a mode store bound to the focus store it coordinates with. Focus save/restore reads/writes
 * that store; nothing else couples the two — the binding is captured once here, not threaded through
 * callers (same pattern as {@link createFocusStore} capturing the panel store).
 */
export function createModeStore(focus: FocusStoreApi): ModeStoreApi {
  const store = createStore<ModeState>()((set, get) => ({
    stack: [],
    enter(mode) {
      const existing = get().stack.find((f) => f.mode.id === mode.id);
      // Preserve the original frame's saved focus on a re-enter (don't save *our own* surface as the
      // restore target); otherwise capture the current effective focus as this frame's restore point.
      const savedFocus = existing?.savedFocus ?? selectEffectiveFocus(focus);
      // The stack stores the erased `Mode<string>` shape (the dispatcher only fires *a* string intent
      // through `onIntent`, always with an intent drawn from this same mode's keymap — so widening
      // `Intent` to `string` on store is sound, exactly as the keymap registry erases `PanelKeymap`).
      const frame: ModeFrame = { mode: mode as unknown as Mode, savedFocus };
      set((state) => ({
        stack: [...state.stack.filter((f) => f.mode.id !== mode.id), frame],
      }));
    },
    exit(id) {
      set((state) => {
        if (state.stack.length === 0) {
          return state;
        }
        const topIndex = state.stack.length - 1;
        const target = id === undefined ? topIndex : state.stack.findIndex((f) => f.mode.id === id);
        if (target < 0) {
          return state; // id not on the stack — no-op
        }
        // Only restore focus when removing the active (top) frame: a buried mode never held live
        // focus, so popping it must not move the highlight out from under the top mode.
        if (target === topIndex) {
          const frame = state.stack[topIndex];
          if (frame !== undefined) {
            focus.getState().focus(frame.savedFocus);
          }
        }
        return { stack: state.stack.filter((_, i) => i !== target) };
      });
    },
  }));
  return Object.assign(store, { focus });
}
