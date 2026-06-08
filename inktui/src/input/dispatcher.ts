/**
 * The single root input dispatcher — the *one* place that reads Ink key events (rule 5). Every key
 * the app handles flows through here; panels never call `useInput`. This replaces the Textual app's
 * central `check_action` gating table and its scattered per-widget `on_key` handlers with one
 * layered, declarative dispatch.
 *
 * ## Layered dispatch order
 *
 * For each `(input, key)` event, the layers are tried in order; the first that claims the event
 * stops dispatch:
 *
 *  0. **Active-mode capture** — if a transient mode is up (a popup dialog, an in-layout editor, a
 *     full-screen view — see {@link ./modeStore.js}), the event is captured and routed to that
 *     mode's *declared* keymap *only*. A matching chord fires the mode's intent (its dismiss key is
 *     just a declared chord); a non-match first consults the mode's optional `onUncaptured` hook
 *     (C12 extension — lets text-input dialogs capture raw printable characters the keymap cannot
 *     wildcard-match; C8 editor uses the same hook). If `onUncaptured` returns `false` or is absent,
 *     the event is **swallowed** so the lower layers (global chords, the focused panel) cannot fire
 *     underneath the modal surface — exclusive capture, the whole point of a mode. The one escape
 *     hatch is `mode.passThrough === true`: then a non-matching key (not consumed by `onUncaptured`)
 *     *falls through* to layers 1–3 (so e.g. a full-screen tmux view can still honour `ctrl+<n>`).
 *     This layer is checked first, before global chords, on purpose: while a modal is up even
 *     `ctrl+<n>` must not summon a panel unless the mode opted into pass-through. A later agent must
 *     not "fix" this back below the chord layer — that would break exclusive capture.
 *  1. **Global chords** — `ctrl+<n>` (toggle/focus a panel), `ctrl+h/j/k/l` (vim directional nav),
 *     `ctrl+y` (tmux toggle), `ctrl+s` (spawn/star), `ctrl+f` (focus chat), `ctrl+p` (new-plan
 *     popup, C12), `ctrl+t` (new-ticket popup, C12). These are app-wide and
 *     always win, *including while chat is focused*, so the user can summon a panel mid-message.
 *     They are safe to check first because every one carries `ctrl`, which printable typing never
 *     does — so checking them ahead of the chat short-circuit cannot swallow a typed character.
 *     (The plan lists "chat short-circuit → global chords"; we resolve the apparent ordering by
 *     scoping the short-circuit to *non-chord* input, which is the only reading that lets `ctrl+<n>`
 *     work while typing. Documented here so a later agent doesn't "fix" it back.)
 *  2. **Chat short-circuit** — if the effective focus is the chat input, the (non-chord) event
 *     belongs to the text field; the dispatcher yields so the chat component's own editing handles
 *     it. The dispatcher claims nothing here beyond declining to route it elsewhere.
 *  3. **Focused panel keymap** — otherwise the event is offered to the focused panel's *declared*
 *     keymap; a matching chord fires that panel's intent. No match → the event is ignored (no
 *     gating decision, just "this panel didn't declare that key").
 *
 * ## Purity
 *
 * The decision is the pure {@link dispatchKey} function over plain data (focus id, panel keymaps,
 * the callbacks for global intents). The React glue ({@link useRootInput}) is a thin `useInput`
 * wrapper that gathers the live data and calls it — so the whole dispatch policy unit-tests with
 * synthesised key events and no rendering, and `ink-testing-library` only needs to prove the wiring.
 */

import type { Key } from 'ink';
import { CHAT_FOCUS, type FocusId } from './focusStore.js';
import type { Direction } from './geometry.js';
import { matchKeymap, type PanelKeymap } from './keymap.js';
import type { Mode } from './modeStore.js';
import { type PanelId, panelForDigit } from './panels.js';

/**
 * C12 augmentation: optional raw-input escape hatch for modes that need to capture printable
 * characters (e.g. text-input dialogs). When a mode is active and its declared keymap does NOT
 * match a key event, `onUncaptured` is called if present. Returning `true` marks the key as
 * handled (still captured by the mode); returning `false` falls through to the normal
 * capture-or-passthrough logic. This is additive — modes that do not need raw char capture simply
 * omit the field; `ConfirmModal` and all existing modes are unaffected.
 *
 * C13 copies this pattern for the spawn wizard's text fields.
 */
declare module './modeStore.js' {
  interface Mode {
    /**
     * Optional raw-input handler, called when the mode's declared keymap produces no match.
     * Return `true` to consume the event (the mode handled it); `false` to leave the normal
     * capture/pass-through logic in charge (swallow if `!passThrough`, fall through if
     * `passThrough === true`).
     */
    onUncaptured?: (input: string, key: Key) => boolean;
  }
}

/** The app-wide intents the global-chord layer can fire. Handed to {@link dispatchKey} as callbacks
 * so the dispatcher stays decoupled from the stores — the wiring hook supplies handlers that drive
 * the focus/panel stores and the (future) tmux/spawn actions. */
export interface GlobalHandlers {
  /** `ctrl+<n>`: bring focus to a panel, toggling it visible first if it is off. */
  focusPanel(id: PanelId): void;
  /** `ctrl+h/j/k/l`: move focus to the geometric neighbour in `direction`. */
  navigate(direction: Direction): void;
  /** `ctrl+f`: focus the chat input. */
  focusChat(): void;
  /** `ctrl+s`: spawn/star context key (the spawn wizard / star, wired by later chunks). */
  spawn(): void;
  /** `ctrl+y`: toggle tmux-vs-parsed view (wired by C14). */
  toggleTmux(): void;
  /** `ctrl+p`: open the new-plan popup (wired by C12). */
  newPlan(): void;
  /** `ctrl+t`: open the new-ticket popup (wired by C12). */
  newTicket(): void;
}

/** The live input context for one key event: where focus is, and the focused panel's keymap (when a
 * panel is focused). `panelKeymaps` maps a visible/focusable panel to what it has declared; the
 * dispatcher reads only the focused panel's entry. */
export interface DispatchContext {
  readonly focusedId: FocusId;
  readonly panelKeymaps: Partial<Record<PanelId, PanelKeymap>>;
  readonly handlers: GlobalHandlers;
  /** The active transient mode, or `null` when none is up. Supplied by {@link useRootInput} from the
   * live {@link ./modeStore.js mode store}. When non-null, layer 0 captures the event (see the
   * layered-dispatch doc above). Kept on the context (not a store reference) so {@link dispatchKey}
   * stays pure — the React glue reads the active mode and passes it in. */
  readonly activeMode: Mode | null;
}

/** The vim navigation chords, as data: `ctrl+<letter>` → direction. Declared here (not inlined in a
 * switch) so the mapping is one table the dispatcher and any help text share. */
const VIM_NAV: Readonly<Record<string, Direction>> = {
  h: 'left',
  j: 'down',
  k: 'up',
  l: 'right',
};

/**
 * Try the global-chord layer. Returns `true` if a global chord claimed the event. Only fires on
 * `ctrl`-modified events, so it never intercepts plain typing. Order within the layer is
 * deterministic: digit toggles, then vim nav, then the single-letter app chords.
 */
function dispatchGlobalChord(input: string, key: Key, handlers: GlobalHandlers): boolean {
  if (!key.ctrl) {
    return false;
  }

  // ctrl+<n>: panel toggle/focus. `panelForDigit` returns null for reserved/unbound digits → no-op.
  const panel = panelForDigit(input);
  if (panel !== null) {
    handlers.focusPanel(panel);
    return true;
  }

  // ctrl+h/j/k/l: directional nav.
  const direction = VIM_NAV[input];
  if (direction !== undefined) {
    handlers.navigate(direction);
    return true;
  }

  // The single-letter app chords.
  switch (input) {
    case 'f':
      handlers.focusChat();
      return true;
    case 's':
      handlers.spawn();
      return true;
    case 'y':
      handlers.toggleTmux();
      return true;
    case 'p':
      // C12: ctrl+p → new-plan popup.
      handlers.newPlan();
      return true;
    case 't':
      // C12: ctrl+t → new-ticket popup.
      handlers.newTicket();
      return true;
    default:
      return false;
  }
}

/**
 * The pure dispatch decision for one key event. Runs the three layers in order and returns what the
 * dispatcher did, so a test can assert the layer that claimed the event without observing side
 * effects only. Side effects (firing an intent/handler) happen as the matched layer is resolved —
 * the return value names the outcome.
 */
export type DispatchOutcome =
  | { readonly layer: 'mode'; readonly handled: boolean }
  | { readonly layer: 'global'; readonly handled: true }
  | { readonly layer: 'chat'; readonly handled: false }
  | { readonly layer: 'panel'; readonly handled: boolean };

export function dispatchKey(input: string, key: Key, ctx: DispatchContext): DispatchOutcome {
  // Layer 0 — active-mode capture. A live mode captures the event exclusively: its declared keymap is
  // tried, and on no match the event is swallowed so no lower layer fires under the modal — UNLESS the
  // mode opts into pass-through, in which case a non-match falls through to layers 1–3.
  //
  // Extension (C12): if the mode's keymap does not match and the mode defines `onUncaptured`, it is
  // called before the swallow/pass-through decision. Returning `true` means the mode consumed it
  // (e.g. a text-input dialog appended the char); returning `false` restores the original behaviour.
  // This is additive — ConfirmModal and all existing modes omit `onUncaptured` and are unaffected.
  if (ctx.activeMode !== null) {
    const intent = matchKeymap(ctx.activeMode.keymap, input, key);
    if (intent !== null) {
      ctx.activeMode.onIntent(intent);
      return { layer: 'mode', handled: true };
    }
    // onUncaptured: let the mode handle a raw key before swallowing (e.g. for text-input fields).
    if (ctx.activeMode.onUncaptured !== undefined) {
      const consumed = ctx.activeMode.onUncaptured(input, key);
      if (consumed) {
        return { layer: 'mode', handled: true };
      }
    }
    if (ctx.activeMode.passThrough !== true) {
      return { layer: 'mode', handled: false }; // captured-but-unmatched: swallow, don't leak down
    }
    // pass-through: fall out of layer 0 into the normal layers below.
  }

  // Layer 1 — global chords (win even while chat is focused; ctrl-only, so typing is safe).
  if (dispatchGlobalChord(input, key, ctx.handlers)) {
    return { layer: 'global', handled: true };
  }

  // Layer 2 — chat short-circuit: a non-chord event while chat is focused belongs to the input.
  // The dispatcher declines to route it; the chat component handles its own editing.
  if (ctx.focusedId === CHAT_FOCUS) {
    return { layer: 'chat', handled: false };
  }

  // Layer 3 — delegate to the focused panel's declared keymap.
  const panelKeymap = ctx.panelKeymaps[ctx.focusedId];
  if (panelKeymap === undefined) {
    return { layer: 'panel', handled: false };
  }
  const intent = matchKeymap(panelKeymap.keymap, input, key);
  if (intent === null) {
    return { layer: 'panel', handled: false };
  }
  panelKeymap.onIntent(intent);
  return { layer: 'panel', handled: true };
}
