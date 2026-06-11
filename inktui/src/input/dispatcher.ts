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
 *     *falls through* to layers 1–3 (so e.g. a full-screen tmux view can still honour `alt+<n>`).
 *     This layer is checked first, before global chords, on purpose: while a modal is up even
 *     `alt+<n>` must not summon a panel unless the mode opted into pass-through. A later agent must
 *     not "fix" this back below the chord layer — that would break exclusive capture.
 *  1. **Global chords** — `alt+<n>` (toggle/focus a panel), `alt+h/j/k/l` (vim directional nav),
 *     `alt+y` (tmux toggle), `alt+s` (spawn wizard — see below), `alt+space` (focus chat), `alt+p`
 *     (new-plan popup, C12), `alt+t` (new-ticket popup, C12). These are app-wide and
 *     always win, *including while chat is focused*, so the user can summon a panel mid-message.
 *     They are safe to check first because every one carries `meta`, which printable typing never
 *     does — so checking them ahead of the chat short-circuit cannot swallow a typed character.
 *     **`alt+s` claims when chat OR a Stage pane is focused:** it opens the spawn wizard when the
 *     effective focus is the chat input or a Stage pane (a chat-history pane / the open doc); with a
 *     *list panel* focused it declines here (returns false) and the event falls through to layer 3.
 *     Panels no longer bind `alt+s` (favorite/star is `alt+f` now), so the fall-through is simply
 *     unhandled at the panel layer. `ctrl+q` (`global.closePane`) is the symmetric "close the
 *     highlighted Stage pane" chord, also a plain chord scoped to Stage-pane focus. See
 *     {@link dispatchGlobalChord}'s doc.
 *     (The plan lists "chat short-circuit → global chords"; we resolve the apparent ordering by
 *     scoping the short-circuit to *non-chord* input, which is the only reading that lets `alt+<n>`
 *     work while typing. Documented here so a later agent doesn't "fix" it back.)
 *
 *     **`alt+f` is NOT a global chord** — it falls through the global layer (the `switch` default
 *     returns false) so the focused panel's keymap can bind it to favorite/star its local cursor row
 *     (rule 1 — the cursor stays panel-local, which the global layer cannot see).
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
import { DEFAULT_BINDINGS, type ResolvedBindings } from './bindings.js';
import { CHAT_FOCUS, type FocusId, isStagePaneId } from './focusStore.js';
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
  /** `alt+<n>`: bring focus to a panel, toggling it visible first if it is off. */
  focusPanel(id: PanelId): void;
  /** `alt+h/j/k/l`: move focus to the geometric neighbour in `direction`. */
  navigate(direction: Direction): void;
  /** `alt+space`: focus the chat input. */
  focusChat(): void;
  /** `alt+s`: open the spawn wizard (only when chat is focused; wired by C13). */
  spawn(): void;
  /** `alt+y`: toggle tmux-vs-parsed view (wired by C14). */
  toggleTmux(): void;
  /** `alt+p`: open the new-plan popup (wired by C12). */
  newPlan(): void;
  /** `alt+t`: open the new-ticket popup (wired by C12). */
  newTicket(): void;
  /** `alt+o` / `ctrl+o` (the `global.settings` action): open the settings modal (wired by Phase 5). */
  openSettings(): void;
  /** `ctrl+n` (the `global.quickNote` action): open the quick-note capture modal. Modifier-independent
   * (a `plain` chord), matched ahead of the command-modifier gate so a ctrl/both setting can't shadow it. */
  quickNote(): void;
  /** `?` (the `global.keyHelp` action): open the keybinding help overlay (item 12). Fires only when
   * chat is NOT focused (so a literal `?` typed into the chat field is never stolen). */
  keyHelp(): void;
  /** `alt+h`/`ctrl+h` (`global.cycleTargetPrev`): cycle the chat target to the previous one. Fires
   * ONLY while chat has focus — otherwise alt+h is geometric panel nav (item 9 super-chords). */
  cycleTargetPrev(): void;
  /** `alt+l`/`ctrl+l` (`global.cycleTargetNext`): cycle the chat target to the next one. Chat-focus
   * only (item 9 super-chords). */
  cycleTargetNext(): void;
  /** `alt+w`/`ctrl+w` (`global.toggleTargetPane`): toggle the current chat target's pane. Chat-focus
   * only (item 9 super-chords). */
  toggleTargetPane(): void;
  /** `ctrl+m` (the `global.murder` action): ARM the two-press murder confirm for the targeted crow
   * (the crow of the focused chat pane, else the active chat target). Fires from any focus EXCEPT the
   * crows panel — there the chord falls through to the panel keymap, which arms with its own local
   * cursor row (the same decline-to-panel pattern as `global.spawn`'s chat-only guard). */
  murder(): void;
  /** Whether a murder confirm is currently armed (pending). Read per-event so the dispatcher's
   * pending check (the layer that claims the confirming `m`) stays pure — the shell supplies a
   * closure over the live pending state. */
  murderPending(): boolean;
  /** Confirm the armed murder — the second press (`m` or ctrl+m) within the pending window. Kills
   * the armed target and clears the pending state. */
  murderConfirm(): void;
  /** Cancel the armed murder. Fired (without consuming the event) when any non-confirm key arrives
   * while pending — the key then keeps its normal meaning in the lower layers. */
  murderCancel(): void;
  /** `ctrl+q` (the `global.closePane` action): close the currently-highlighted Stage pane — the open
   * doc pane (`stage:doc:<name>`) or a chat-history pane (`stage:chat:<agentId>`). Fired ONLY when the
   * effective focus is a Stage pane; from chat/a panel the chord falls through (does nothing). The
   * closed pane unmounts → focus re-homes to chat via the derived invariant (no imperative re-home). */
  closePane(): void;
}

/**
 * The chat-input handler — the **persistent chat-input mode** (C11, part F), expressed as a layer-2
 * callback rather than a {@link ./modeStore.js modeStore} frame. Chat is the app's permanent focus
 * home: there is nothing to save/restore and nothing to dismiss, so it is NOT a transient mode (the
 * modeStore contract is capture + focus-restore, which chat does not want). Instead, when the chat
 * input is the effective focus, the dispatcher's layer 2 routes the (non-chord) event here. The
 * handler buffers printable characters, sends on `return`, and reports whether it consumed the key.
 *
 * It sees the event ONLY after layer 1 (global alt-chords) has had its chance — so `alt+<n>`,
 * `alt+s` (spawn wizard, since chat is focused), `alt+y`, etc. still fire while the user is typing (every
 * global chord carries `meta`, which printable typing never does). That ordering is why the persistent
 * chat mode needs no special escape hatch: the global layer already preempts it.
 */
export interface ChatInputHandler {
  /** Handle one chat key event. Return `true` if consumed (a char buffered, or a send fired), so the
   * dispatcher reports `handled: true`; `false` to leave it unhandled (e.g. an unmapped control key). */
  handleKey(input: string, key: Key): boolean;
}

/** The live input context for one key event: where focus is, and the focused panel's keymap (when a
 * panel is focused). `panelKeymaps` maps a visible/focusable panel to what it has declared; the
 * dispatcher reads only the focused panel's entry. */
export interface DispatchContext {
  readonly focusedId: FocusId;
  readonly panelKeymaps: Partial<Record<FocusId, PanelKeymap>>;
  readonly handlers: GlobalHandlers;
  /** The active transient mode, or `null` when none is up. Supplied by {@link useRootInput} from the
   * live {@link ./modeStore.js mode store}. When non-null, layer 0 captures the event (see the
   * layered-dispatch doc above). Kept on the context (not a store reference) so {@link dispatchKey}
   * stays pure — the React glue reads the active mode and passes it in. */
  readonly activeMode: Mode | null;
  /** The persistent chat-input handler (C11). Optional: when absent, layer 2 yields as before (the
   * dispatcher declines to route, claiming nothing) — so chunks/tests that don't wire chat input are
   * unaffected. When present, layer 2 routes chat-focused non-chord events to it. */
  readonly chatInput?: ChatInputHandler;
  /**
   * The resolved binding table (see {@link ./bindings.js}). The dispatcher reads it to (a) gate the
   * digit/vim-nav layer via {@link ResolvedBindings.isCommandModified} instead of a hardcoded
   * `key.meta`, and (b) match the named global chords via {@link ResolvedBindings.matches} — so the
   * command modifier (alt/ctrl/both) and any rebinds are honoured without the dispatcher knowing
   * which modifier is in play (a deep module). Optional: when absent, {@link DEFAULT_BINDINGS}
   * (today's alt behavior) is used, so existing call sites/tests need no change.
   */
  readonly bindings?: ResolvedBindings;
}

/** The vim navigation chords, as data: `alt+<letter>` → direction. Declared here (not inlined in a
 * switch) so the mapping is one table the dispatcher and any help text share. */
const VIM_NAV: Readonly<Record<string, Direction>> = {
  h: 'left',
  j: 'down',
  k: 'up',
  l: 'right',
};

/**
 * Try the global-chord layer. Returns `true` if a global chord claimed the event. Only fires on
 * `meta`(alt)-modified events, so it never intercepts plain typing. Order within the layer is
 * deterministic: digit toggles, then vim nav, then the single-letter app chords.
 *
 * ## `alt+s` claims the event when chat OR a Stage pane is focused
 *
 * Every *other* global chord wins unconditionally (it carries `meta`, so it can't swallow typing).
 * `alt+s` is the documented exception: it opens the spawn wizard when the effective focus is the chat
 * input OR a Stage pane (a chat-history pane `stage:chat:<agentId>` or the open doc `stage:doc:<name>`).
 * When a *list panel* is focused we return `false` for `'s'`, letting it fall through to layer 3 —
 * panels no longer bind `alt+s` (favorite/star moved to `alt+f`), so it is simply unhandled there.
 * Keeping the chat-or-Stage guard means `alt+s` never fires the spawn wizard from a list panel, while
 * still letting the user spawn from a highlighted chat-history or doc pane (the stagelayout plan's
 * requirement). The doc-vs-chat file-context decision is made by the spawn handler reading the
 * effective focus (see {@link ../components/App.js}'s `deriveSpawnContext`), NOT here — the dispatcher
 * only routes the chord. A later agent must not "fix" this back to chat-only: spawning from a
 * highlighted Stage pane is the locked user decision.
 *
 * `alt+f` (favorite/star) is handled entirely by the panel layer — it is intentionally absent from
 * this global switch (the `default` returns false), so it falls through to the focused panel's
 * keymap, which stars the panel's own local cursor row (rule 1 — the cursor stays panel-local).
 */
function dispatchGlobalChord(
  input: string,
  key: Key,
  handlers: GlobalHandlers,
  focusedId: FocusId,
  bindings: ResolvedBindings,
): boolean {
  // The murder pending check — FIRST, even ahead of the other plain chords, because while armed the
  // bare `m` is the confirm press and must not reach the chat field (typing) or a panel keymap
  // (CrowsPanel's min/max toggle). This is the one sanctioned exception to "the global layer never
  // claims plain typing": it is gated on `murderPending()`, a window the user just opened with ctrl+m
  // and that self-expires in ~3s. Any OTHER key cancels the pending state and falls through with its
  // normal meaning (it is NOT consumed — esc still closes a doc, a letter still types).
  if (handlers.murderPending()) {
    const confirmByM = input === 'm' && key.ctrl !== true && key.meta !== true;
    if (confirmByM || bindings.matches('global.murder', input, key)) {
      handlers.murderConfirm();
      return true;
    }
    handlers.murderCancel();
  }

  // `global.murder` (ctrl+m, a plain chord) ARMS the confirm. Matched before the command-modifier
  // gate (like quickNote — under a ctrl/both modifier the gate would swallow it). The crows panel is
  // the documented decline: with `crows` focused the chord falls through to the panel keymap, which
  // arms with its own LOCAL cursor row (rule 1 — the global layer cannot see panel cursors; the same
  // decline-to-panel pattern as `global.spawn`'s chat-only guard).
  if (bindings.matches('global.murder', input, key)) {
    if (focusedId === 'crows') {
      return false;
    }
    handlers.murder();
    return true;
  }

  // `global.quickNote` (ctrl+n) is a `plain` chord, matched BEFORE the command-modifier gate so a
  // `modifier=ctrl`/`both` setting can't shadow it: under those settings `isCommandModified` is true
  // for any ctrl event, and the gate below would route ctrl+n into the digit/vim/named-command branch
  // where it has no entry — silently swallowing it. Checking the explicit plain chord first keeps
  // ctrl+n reaching the note capture under every modifier choice. It carries ctrl, which plain typing
  // never does, so checking it ahead of typing is safe (same property the rest of the layer relies on).
  if (bindings.matches('global.quickNote', input, key)) {
    handlers.quickNote();
    return true;
  }

  // `global.closePane` (ctrl+q, a `plain` chord) closes the currently-highlighted Stage pane (a chat-
  // history pane or the open doc). Matched BEFORE the command-modifier gate (like quickNote — under a
  // ctrl/both modifier the gate would otherwise route ctrl+q into the digit/named-command branch and
  // swallow it). It claims the event ONLY when a Stage pane holds the effective focus; from chat or a
  // list panel it DECLINES (returns false → falls through), so ctrl+q does nothing there rather than a
  // surprising close. There is ONE close mechanism for both pane kinds (chat panes have no close key of
  // their own); a later agent must not move this onto per-pane keymaps. ctrl+q carries ctrl, which plain
  // typing never does, so checking it ahead of typing is safe.
  if (bindings.matches('global.closePane', input, key)) {
    if (isStagePaneId(focusedId)) {
      handlers.closePane();
      return true;
    }
    return false;
  }

  // Item 12: the keybinding help overlay (`global.keyHelp`, a *plain* `?` — no command modifier, so it
  // is reachable in every terminal). It claims the event ONLY when chat is NOT focused, so a literal
  // `?` typed into the chat field is never stolen (chat-focused `?` falls through to layer 2). Checked
  // before the command-modifier gate precisely because it is a plain key, not a command chord.
  if (focusedId !== CHAT_FOCUS && bindings.matches('global.keyHelp', input, key)) {
    handlers.keyHelp();
    return true;
  }

  // The command modifier (alt by default; ctrl/both via settings) gates the whole layer. The
  // registry knows which flag(s) qualify, so this is no longer a hardcoded `key.meta` — but the
  // safety property is unchanged: the command modifier is never set by plain typing, so checking
  // these first can't swallow a typed character.
  if (!bindings.isCommandModified(key)) {
    return false;
  }

  // Item 9 super-chords — chat-target cycling + pane toggle, active ONLY while the chat input has
  // focus. Gated here (the chat-focus branch of the global layer), NOT as unconditional globals,
  // because away from chat the same `alt+h`/`alt+l` are geometric panel nav (handled just below by
  // VIM_NAV) and `alt+w` is unbound. Checked before VIM_NAV so the cycle chords win over nav while
  // typing a message.
  if (focusedId === CHAT_FOCUS) {
    if (bindings.matches('global.cycleTargetPrev', input, key)) {
      handlers.cycleTargetPrev();
      return true;
    }
    if (bindings.matches('global.cycleTargetNext', input, key)) {
      handlers.cycleTargetNext();
      return true;
    }
    if (bindings.matches('global.toggleTargetPane', input, key)) {
      handlers.toggleTargetPane();
      return true;
    }
  }

  // <mod>+<n>: panel toggle/focus. `panelForDigit` returns null for reserved/unbound digits → no-op.
  const panel = panelForDigit(input);
  if (panel !== null) {
    handlers.focusPanel(panel);
    return true;
  }

  // <mod>+h/j/k/l: directional nav.
  const direction = VIM_NAV[input];
  if (direction !== undefined) {
    handlers.navigate(direction);
    return true;
  }

  // The named single-purpose app chords, matched against the resolved bindings (so a rebind or a
  // different modifier is honoured without touching this code).
  if (bindings.matches('global.focusChat', input, key)) {
    // focus the chat input (was alt+f, which now stars in panels).
    handlers.focusChat();
    return true;
  }
  if (bindings.matches('global.spawn', input, key)) {
    // Spawn wizard when chat OR a Stage pane (chat-history / doc) holds focus (see the fn doc);
    // otherwise (a list panel) decline (return false → falls through to layer 3, where panels no
    // longer bind the spawn chord, so it is unhandled). The spawn handler reads the effective focus to
    // decide whether to include the doc file in context (doc pane → yes; chat pane → no).
    if (focusedId === CHAT_FOCUS || isStagePaneId(focusedId)) {
      handlers.spawn();
      return true;
    }
    return false;
  }
  if (bindings.matches('global.tmux', input, key)) {
    handlers.toggleTmux();
    return true;
  }
  if (bindings.matches('global.newPlan', input, key)) {
    // C12: new-plan popup.
    handlers.newPlan();
    return true;
  }
  if (bindings.matches('global.newTicket', input, key)) {
    // C12: new-ticket popup.
    handlers.newTicket();
    return true;
  }
  if (bindings.matches('global.settings', input, key)) {
    // Phase 5: the settings modal. Like the other app chords it wins app-wide (it carries the
    // command modifier, so it never swallows typing) — opens the settings modal from any focus.
    handlers.openSettings();
    return true;
  }
  return false;
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
  | { readonly layer: 'chat'; readonly handled: boolean }
  | { readonly layer: 'panel'; readonly handled: boolean };

export function dispatchKey(input: string, key: Key, ctx: DispatchContext): DispatchOutcome {
  // Default to today's alt behavior when a context omits bindings (existing call sites/tests) — the
  // zero-behavior-change guarantee. Production wires the live resolved table from the bindings store.
  const bindings = ctx.bindings ?? DEFAULT_BINDINGS;
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

  // Layer 1 — global chords (win even while chat is focused; meta-only, so typing is safe). The
  // focus-scoped exceptions are `alt+s` (spawn — claims when chat OR a Stage pane is focused, declines
  // on a list panel so alt+f stays the panel favorite/star chord) and `ctrl+q` (`global.closePane` —
  // claims ONLY when a Stage pane is focused). So the focus id is passed in. See dispatchGlobalChord's doc.
  if (dispatchGlobalChord(input, key, ctx.handlers, ctx.focusedId, bindings)) {
    return { layer: 'global', handled: true };
  }

  // Layer 2 — chat short-circuit: a non-chord event while chat is focused belongs to the input. C11:
  // route it to the persistent chat-input handler (the "persistent chat mode"), if one is wired —
  // it buffers printable chars and sends on Enter. Global alt-chords already had their turn in layer
  // 1, so this only ever sees the events that genuinely belong to the text field. When no handler is
  // wired (older chunks/tests), the dispatcher declines as before, claiming nothing.
  if (ctx.focusedId === CHAT_FOCUS) {
    if (ctx.chatInput !== undefined) {
      const consumed = ctx.chatInput.handleKey(input, key);
      return { layer: 'chat', handled: consumed };
    }
    return { layer: 'chat', handled: false };
  }

  // Layer 3 — delegate to the focused panel's declared keymap.
  const panelKeymap = ctx.panelKeymaps[ctx.focusedId];
  if (panelKeymap === undefined) {
    return { layer: 'panel', handled: false };
  }
  // A coalesced printable run (fast typing over a slow pty, tmux send-keys, paste) reaches Ink as
  // ONE event whose `input` is the whole string — which a single-key chord can never match, so the
  // run would be silently dropped. Split it and offer each char: two fast `j`s must scroll twice,
  // and `g3` must start-then-extend the go-to-line capture (whose digit entries are pre-registered
  // for exactly this — all chars match against the same per-event keymap snapshot). Safe here at the
  // bottom layer only: modes (layer 0) and the chat field (layer 2) already had the full string —
  // text input is never split.
  if (input.length > 1) {
    let handledAny = false;
    for (const ch of input) {
      const charIntent = matchKeymap(panelKeymap.keymap, ch, key);
      if (charIntent !== null) {
        panelKeymap.onIntent(charIntent);
        handledAny = true;
      }
    }
    return { layer: 'panel', handled: handledAny };
  }
  const intent = matchKeymap(panelKeymap.keymap, input, key);
  if (intent === null) {
    return { layer: 'panel', handled: false };
  }
  panelKeymap.onIntent(intent);
  return { layer: 'panel', handled: true };
}
