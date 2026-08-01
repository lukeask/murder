/**
 * Chord-label prefix + live KeybindBar hints from `resolveBindings` / `selectBottomBar`
 * (tracks modifier + key_overrides like the TUI bottom bar).
 */

import { resolveBindings, type ActionId } from '@murder/ui-core/input/bindings.js';
import { CHAT_FOCUS } from '@murder/ui-core/input/focusStore.js';
import { selectBottomBar } from '@murder/ui-core/selectors/barSelectors.js';
import type { SettingsModifier } from '@murder/ui-core/store/settings/settingsSlice.js';
import type { KeybindHint } from './components/ds/index.js';

/** Prefix for chord chips: `A-`, `C-`, or `A-/C-` when both modifiers are live. */
export function commandModifierPrefix(modifier: SettingsModifier): string {
  if (modifier === 'alt') return 'A-';
  if (modifier === 'ctrl') return 'C-';
  return 'A-/C-';
}

/**
 * Desktop KeybindBar hints — live from {@link resolveBindings} + {@link selectBottomBar} (chat focus).
 * Browser always delivers Ctrl, so `ctrlAvailable` is true (unlike a bare TTY without kitty).
 */
export function desktopKeybindHints(
  modifier: SettingsModifier,
  overrides: Readonly<Partial<Record<ActionId, string>>> = {},
): readonly KeybindHint[] {
  const bindings = resolveBindings(modifier, true, overrides);
  return selectBottomBar(CHAT_FOCUS, undefined, bindings).map((hint) => ({
    chord: hint.key,
    desc: hint.description,
  }));
}
