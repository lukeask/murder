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
 *     (new-plan popup, C12), `alt+t` (chat-view cycle). These are app-wide and
 *     always win, *including while chat is focused*, so the user can summon a panel mid-message.
 *     They are safe to check first because every one carries `meta`, which printable typing never
 *     does — so checking them ahead of the chat short-circuit cannot swallow a typed character.
 *     **`alt+s` claims when chat OR a Stage pane is focused:** it opens the spawn wizard when the
 *     effective focus is the chat input or a Stage pane (a transcript pane / the open doc); with a
 *     *list panel* focused it declines here (returns false) and the event falls through to layer 3.
 *     Panels no longer bind `alt+s` (favorite/star is `alt+f` now), so the fall-through is simply
 *     unhandled at the panel layer. `alt+w`/`ctrl+w` (`global.toggleTargetPane`) toggles show/hide
 *     for the active transcript or doc pane when chat OR a Stage pane is focused. See
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

import { type ActionId, DEFAULT_BINDINGS, type ResolvedBindings } from './bindings.js';
import { CHAT_FOCUS, type FocusId } from './focusStore.js';
import type { Direction } from './geometry.js';
import { GLOBAL_SCOPE, type FocusScope, inFocusScope } from './globalScope.js';
import { matchKeymap, type Key, type PanelKeymap } from './keymap.js';
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
export interface TransientMode extends PanelKeymap {
  readonly passThrough?: boolean;
  readonly captureCtrlC?: boolean;
  onUncaptured?: (input: string, key: Key) => boolean;
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
  /** `alt+t` / `ctrl+t` (`global.cycleChatView`, TUIchat-3): cycle the focused transcript pane through
   * verbose → condensed → tmux. Resolves the focused pane's agentId and calls `cyclePaneViewMode`. */
  cycleChatView(): void;
  /** `alt+p`: open the new-plan popup (wired by C12). */
  newPlan(): void;
  /** `alt+g` / `ctrl+g`: open the workflow template editor. */
  openWorkflowTemplateEditor?(): void;
  /** New-ticket popup. CHORD-LESS since TUIchat-3 (it lost `t` to the chat-view cycle; ticket-redo
   * rehomes it). The handler stays wired so a future chord/command can reach it. */
  newTicket(): void;
  /** `alt+o` / `ctrl+o` (the `global.settings` action): open the settings modal (wired by Phase 5). */
  openSettings(): void;
  /** `ctrl+n` (the `global.quickNote` action): open the quick-note capture modal. Modifier-independent
   * (a `plain` chord), matched ahead of the command-modifier gate so a ctrl/both setting can't shadow it. */
  quickNote(): void;
  /** `?` (the `global.keyHelp` action): open the keybinding help overlay (item 12). Fires only when
   * chat is NOT focused (so a literal `?` typed into the chat field is never stolen). */
  keyHelp(): void;
  /** `alt+h`/`ctrl+h` (`global.cycleTargetPrev`): cycle the recipient target to the previous one. Fires
   * ONLY while chat has focus — otherwise alt+h is geometric panel nav (item 9 super-chords). */
  cycleTargetPrev(): void;
  /** `alt+l`/`ctrl+l` (`global.cycleTargetNext`): cycle the recipient target to the next one. Chat-focus
   * only (item 9 super-chords). */
  cycleTargetNext(): void;
  /** `ctrl+j` (`global.toggleTargetGroup`): toggle the recipient target between locked-visible and
   * favorite-only groups. Chat-focus only. */
  toggleTargetGroup?(): void;
  /** `alt+w`/`ctrl+w` (`global.toggleTargetPane`): toggle show/hide for the active transcript or doc
   * pane. From chat: toggle the current recipient target's transcript pane. From a Stage pane: hide the
   * focused transcript or doc pane (chat-or-stage scope). */
  toggleTargetPane(): void;
  /** `ctrl+m` (the `global.murder` action): ARM the two-press murder confirm for the targeted crow
   * (the crow of the focused transcript pane, else the active recipient target). Fires from any focus EXCEPT the
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
  /** `ctrl+r` (`global.repaint`): force a full terminal redraw. Modifier-independent plain chord;
   * ctrl+l is taken by target cycling / panel nav. */
  repaint(): void;
  /** `<Cmd>+Shift+J` (`workspace.next`): cycle to the next workspace (wrapping). */
  workspaceNext?(): void;
  /** `<Cmd>+Shift+K` (`workspace.prev`): cycle to the previous workspace (wrapping). */
  workspacePrev?(): void;
  /** `<Cmd>+Shift+<n>` (`workspace.jump.<n>`): jump to workspace `index` (0-based). No-op when
   * `index >= count`. */
  workspaceJump?(index: number): void;
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
  readonly activeMode: TransientMode | null;
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

/** When a matched global chord is out of scope: `fall-through` stops rule processing and lets lower
 * layers handle the key (panel for murder/spawn); `skip` continues to later rules / the command gate. */
export type DeclineBehavior = 'fall-through' | 'skip';

/** One ordered global-chord rule. Array position is precedence; scope is derived from {@link GLOBAL_SCOPE}. */
export interface GlobalRule {
  readonly id: ActionId;
  readonly scope: FocusScope;
  readonly onDecline: DeclineBehavior;
  run(handlers: GlobalHandlers, input: string): void;
}

function globalRule(
  id: keyof typeof GLOBAL_SCOPE,
  onDecline: DeclineBehavior,
  run: GlobalRule['run'],
): GlobalRule {
  return { id, scope: GLOBAL_SCOPE[id], onDecline, run };
}

/** Nine `workspace.jump.<n>` rules from one parameterized family (index 0-based in the handler). */
const WORKSPACE_JUMP_RULES: readonly GlobalRule[] = Array.from({ length: 9 }, (_, index) => {
  const ordinal = index + 1;
  const id = `workspace.jump.${ordinal}` as keyof typeof GLOBAL_SCOPE;
  return globalRule(id, 'skip', (handlers) => {
    handlers.workspaceJump?.(index);
  });
});

/** Plain chords matched before the command-modifier gate (ctrl+n, ctrl+r, shifted workspace, etc.). */
export const PLAIN_GLOBAL_RULES: readonly GlobalRule[] = [
  globalRule('global.murder', 'fall-through', (handlers) => {
    handlers.murder();
  }),
  globalRule('global.quickNote', 'skip', (handlers) => {
    handlers.quickNote();
  }),
  globalRule('global.repaint', 'skip', (handlers) => {
    handlers.repaint();
  }),
  globalRule('workspace.next', 'skip', (handlers) => {
    handlers.workspaceNext?.();
  }),
  globalRule('workspace.prev', 'skip', (handlers) => {
    handlers.workspacePrev?.();
  }),
  ...WORKSPACE_JUMP_RULES,
  globalRule('global.toggleTargetGroup', 'skip', (handlers) => {
    handlers.toggleTargetGroup?.();
  }),
  globalRule('global.keyHelp', 'skip', (handlers) => {
    handlers.keyHelp();
  }),
];

/** Command-modified named chords — after {@link ResolvedBindings.isCommandModified} gate. */
export const MODIFIED_GLOBAL_RULES: readonly GlobalRule[] = [
  globalRule('global.toggleTargetPane', 'skip', (handlers) => {
    handlers.toggleTargetPane();
  }),
  globalRule('global.cycleTargetPrev', 'skip', (handlers) => {
    handlers.cycleTargetPrev();
  }),
  globalRule('global.cycleTargetNext', 'skip', (handlers) => {
    handlers.cycleTargetNext();
  }),
  globalRule('global.focusChat', 'skip', (handlers) => {
    handlers.focusChat();
  }),
  globalRule('global.spawn', 'fall-through', (handlers) => {
    handlers.spawn();
  }),
  globalRule('global.cycleChatView', 'skip', (handlers) => {
    handlers.cycleChatView();
  }),
  globalRule('global.newPlan', 'skip', (handlers) => {
    handlers.newPlan();
  }),
  globalRule('global.workflowEditor', 'skip', (handlers) => {
    handlers.openWorkflowTemplateEditor?.();
  }),
  globalRule('global.settings', 'skip', (handlers) => {
    handlers.openSettings();
  }),
];

function tryGlobalRules(
  rules: readonly GlobalRule[],
  input: string,
  key: Key,
  handlers: GlobalHandlers,
  focusedId: FocusId,
  bindings: ResolvedBindings,
): ActionId | null {
  for (const rule of rules) {
    if (!bindings.matches(rule.id, input, key)) {
      continue;
    }
    if (!inFocusScope(rule.scope, focusedId)) {
      if (rule.onDecline === 'fall-through') {
        return null;
      }
      continue;
    }
    rule.run(handlers, input);
    return rule.id;
  }
  return null;
}

/**
 * Try the global-chord layer. Returns the matched {@link ActionId} when a named global chord claims
 * the event, or `null` when nothing matched (digit toggles and vim nav also return `null` but set
 * `handledWithoutAction` — they have no registry id). Only fires on `meta`(alt)-modified events for
 * the command-gated chords, so it never intercepts plain typing. Order within the layer is
 * deterministic: digit toggles, then vim nav, then the single-letter app chords.
 *
 * ## `alt+s` claims the event when chat OR a Stage pane is focused
 *
 * Every *other* global chord wins unconditionally (it carries `meta`, so it can't swallow typing).
 * `alt+s` is the documented exception: it opens the spawn wizard when the effective focus is the chat
 * input OR a Stage pane (a transcript pane or the open doc).
 * When a *list panel* is focused we return `false` for `'s'`, letting it fall through to layer 3 —
 * panels no longer bind `alt+s` (favorite/star moved to `alt+f`), so it is simply unhandled there.
 * Keeping the chat-or-Stage guard means `alt+s` never fires the spawn wizard from a list panel, while
 * still letting the user spawn from a highlighted transcript or doc pane (the stagelayout plan's
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
  /** Digit toggles and vim nav fire here but have no {@link ActionId}; the caller reads this when the
   * return is `null` to distinguish handled-without-action from not-handled. */
  handledWithoutAction: { current: boolean },
): ActionId | null {
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
      return 'global.murder';
    }
    handlers.murderCancel();
  }

  const plainAction = tryGlobalRules(
    PLAIN_GLOBAL_RULES,
    input,
    key,
    handlers,
    focusedId,
    bindings,
  );
  if (plainAction !== null) {
    return plainAction;
  }

  // The command modifier (alt by default; ctrl/both via settings) gates the modified-command group.
  // Plain chords above are matched first so ctrl+n / ctrl+r / shifted workspace chords are never
  // shadowed by the gate.
  if (!bindings.isCommandModified(key)) {
    return null;
  }

  const modifiedAction = tryGlobalRules(
    MODIFIED_GLOBAL_RULES,
    input,
    key,
    handlers,
    focusedId,
    bindings,
  );
  if (modifiedAction !== null) {
    return modifiedAction;
  }

  // <mod>+<n>: panel toggle/focus. `panelForDigit` returns null for reserved/unbound digits → no-op.
  const panel = panelForDigit(input);
  if (panel !== null) {
    handlers.focusPanel(panel);
    handledWithoutAction.current = true;
    return null;
  }

  // <mod>+h/j/k/l: directional nav.
  const direction = VIM_NAV[input];
  if (direction !== undefined) {
    handlers.navigate(direction);
    handledWithoutAction.current = true;
    return null;
  }

  return null;
}

/**
 * The pure dispatch decision for one key event. Runs the three layers in order and returns what the
 * dispatcher did, so a test can assert the layer that claimed the event without observing side
 * effects only. Side effects (firing an intent/handler) happen as the matched layer is resolved —
 * the return value names the outcome.
 */
export type DispatchOutcome =
  | { readonly layer: 'mode'; readonly handled: boolean; readonly action?: string }
  | { readonly layer: 'global'; readonly handled: true; readonly action?: string }
  | { readonly layer: 'chat'; readonly handled: boolean; readonly action?: string }
  | { readonly layer: 'panel'; readonly handled: boolean; readonly action?: string };

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
      return { layer: 'mode', handled: true, action: `mode:${intent}` };
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
  // on a list panel so alt+f stays the panel favorite/star chord) and `alt+w`/`ctrl+w`
  // (`global.toggleTargetPane` — claims when chat OR a Stage pane is focused). So the focus id is
  // passed in. See dispatchGlobalChord's doc.
  const handledWithoutAction = { current: false };
  const globalAction = dispatchGlobalChord(
    input,
    key,
    ctx.handlers,
    ctx.focusedId,
    bindings,
    handledWithoutAction,
  );
  if (globalAction !== null) {
    return { layer: 'global', handled: true, action: globalAction };
  }
  if (handledWithoutAction.current) {
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
  // Escape (when the panel did not claim it) restores composer focus — same contract as web
  // `panelFocusStore.clear()` on Esc. Panels that bind Escape for a local dismiss/cancel keep that
  // binding via the keymap match below.
  const restoreComposerOnEscape = (): DispatchOutcome | null => {
    if (key.escape !== true) {
      return null;
    }
    ctx.handlers.focusChat();
    return { layer: 'panel', handled: true, action: 'global.focusChat' };
  };
  const panelKeymap = ctx.panelKeymaps[ctx.focusedId];
  if (panelKeymap === undefined) {
    return restoreComposerOnEscape() ?? { layer: 'panel', handled: false };
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
    let lastIntent: string | null = null;
    for (const ch of input) {
      const charIntent = matchKeymap(panelKeymap.keymap, ch, key);
      if (charIntent !== null) {
        panelKeymap.onIntent(charIntent);
        handledAny = true;
        lastIntent = charIntent;
      }
    }
    if (!handledAny) {
      return restoreComposerOnEscape() ?? { layer: 'panel', handled: false };
    }
    return { layer: 'panel', handled: true, action: `${ctx.focusedId}:${lastIntent}` };
  }
  const intent = matchKeymap(panelKeymap.keymap, input, key);
  if (intent === null) {
    return restoreComposerOnEscape() ?? { layer: 'panel', handled: false };
  }
  panelKeymap.onIntent(intent);
  return { layer: 'panel', handled: true, action: `${ctx.focusedId}:${intent}` };
}
