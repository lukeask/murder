/**
 * The focus scope of each *global* chord — the single source of truth for "is this global usable from
 * the current focus?". Both the root dispatcher ({@link ./dispatcher.js}) and the bottom bar's hint
 * view-model ({@link ../selectors/barSelectors.js}) read this table, so a usable chord can never be
 * missing from the bar and a dead chord can never be shown. Before this table the dispatcher inlined
 * the focus gating as scattered `if`s while the bar hard-coded a three-entry global list, and the two
 * drifted: most globals (spawn, settings, the chat super-chords, toggle-pane, …) were live but
 * un-hinted. Keeping the gate as data, consumed by both, makes that drift unrepresentable.
 *
 * Panel-scoped (`panel.*`) actions are intentionally absent: those are gated by *which panel
 * registered them* and surface through the focused panel's keymap (keymap.ts), not here.
 */

import type { ActionId } from './bindings.js';
import { CHAT_FOCUS, type FocusId, isStagePaneId } from './focusStore.js';

/**
 * Where a global chord is live. Mirrors {@link ./dispatcher.js}'s `dispatchGlobalChord` gating:
 *  - `always`        — wins from any focus (carries the command modifier / a ctrl byte, so it never
 *                      shadows typing).
 *  - `not-chat`      — every focus except the chat input (e.g. `?` help, which a chat-focused `?`
 *                      must type instead of stealing).
 *  - `chat`          — only while the chat input is focused (the item-9 chat-target super-chords;
 *                      away from chat the same `alt+h/l` are geometric nav).
 *  - `chat-or-stage` — the chat input OR a Stage pane (transcript / open doc) — `alt+s` spawn.
 *  - `stage`         — only a Stage pane (reserved; no globals use this scope today).
 *  - `not-crows`     — any focus except the crows panel, where the chord falls to that panel's own
 *                      keymap (the `ctrl+m` murder arm).
 */
export type FocusScope = 'always' | 'not-chat' | 'chat' | 'chat-or-stage' | 'stage' | 'not-crows';

/** True iff a chord with `scope` is live from `focused`. The one predicate both consumers use, so the
 * dispatcher's gating and the bar's visibility are computed by identical logic. */
export function inFocusScope(scope: FocusScope, focused: FocusId): boolean {
  switch (scope) {
    case 'always':
      return true;
    case 'not-chat':
      return focused !== CHAT_FOCUS;
    case 'chat':
      return focused === CHAT_FOCUS;
    case 'chat-or-stage':
      return focused === CHAT_FOCUS || isStagePaneId(focused);
    case 'stage':
      return isStagePaneId(focused);
    case 'not-crows':
      return focused !== 'crows';
  }
}

/**
 * The focus scope of every global action, mirroring `dispatchGlobalChord` exactly. Declaration order
 * is the order the bottom bar lists the globals (after its synthetic panels/nav hints). `global.keyHelp`
 * is included for the dispatcher's gate, but the bar renders it as its own right-pinned help hint
 * (with the chat-focus `?`-would-type disambiguation), so the bar skips it when iterating this table.
 */
export const GLOBAL_SCOPE = {
  'global.focusChat': 'always',
  'global.spawn': 'chat-or-stage',
  'global.cycleChatView': 'always',
  'global.newPlan': 'always',
  'global.settings': 'always',
  'global.quickNote': 'always',
  'global.keyHelp': 'not-chat',
  'global.cycleTargetPrev': 'chat',
  'global.toggleTargetGroup': 'chat',
  'global.cycleTargetNext': 'chat',
  'global.toggleTargetPane': 'chat-or-stage',
  'global.murder': 'not-crows',
  'global.repaint': 'always',
  'workspace.next': 'always',
  'workspace.prev': 'always',
  'workspace.jump.1': 'always',
  'workspace.jump.2': 'always',
  'workspace.jump.3': 'always',
  'workspace.jump.4': 'always',
  'workspace.jump.5': 'always',
  'workspace.jump.6': 'always',
  'workspace.jump.7': 'always',
  'workspace.jump.8': 'always',
  'workspace.jump.9': 'always',
} as const satisfies Partial<Record<ActionId, FocusScope>>;

/** An action id that carries a {@link FocusScope} (i.e. a global, not a `panel.*` action). */
export type GlobalActionId = keyof typeof GLOBAL_SCOPE;

/** The global action ids in declaration (and bottom-bar display) order. */
export const GLOBAL_ACTION_IDS = Object.keys(GLOBAL_SCOPE) as readonly GlobalActionId[];
