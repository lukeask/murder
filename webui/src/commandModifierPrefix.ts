/**
 * Chord-label prefix + live KeybindBar hints from `resolveBindings` / `selectBottomBar`
 * (tracks modifier + key_overrides like the TUI bottom bar). Context-adaptive: when a rail
 * panel holds focus, panel keymap hints join the globals; when a modal owns the bar, modeHints
 * replace panel/help; when stage/chat is home, stage scroll/goto chords join chat globals.
 */

import { chordLabel, resolveBindings, type ActionId } from '@murder/ui-core/input/bindings.js';
import { CHAT_FOCUS, type FocusId } from '@murder/ui-core/input/focusStore.js';
import type { Keymap } from '@murder/ui-core/input/keymap.js';
import { selectBottomBar } from '@murder/ui-core/selectors/barSelectors.js';
import type { SettingsModifier } from '@murder/ui-core/store/settings/settingsSlice.js';
import type { KeybindHint } from './components/ds/index.js';
import type { FocusablePanelId } from './panelFocus.js';
import {
  keymapForFocusedPanel,
  WEB_STAGE_DOC_KEYMAP,
  WEB_STAGE_TRANSCRIPT_KEYMAP,
} from './panelHelpKeymaps.js';
import type { ModeHint } from './keybindModeHints.js';

/** Prefix for chord chips: `A-`, `C-`, or `A-/C-` when both modifiers are live. */
export function commandModifierPrefix(modifier: SettingsModifier): string {
  if (modifier === 'alt') return 'A-';
  if (modifier === 'ctrl') return 'C-';
  return 'A-/C-';
}

/** Which stage surface owns scroll chords when no rail panel is focused. */
export type StageHintSurface = 'doc' | 'transcript' | null;

/** Map web panel focus → FocusId for {@link selectBottomBar} (`settings` / null → chat). */
export function focusIdForBottomBar(focusedId: FocusablePanelId | null): FocusId {
  if (focusedId === null || focusedId === 'settings') return CHAT_FOCUS;
  return focusedId;
}

function toHints(
  rows: readonly { readonly key: string; readonly description: string }[],
): KeybindHint[] {
  return rows.map((hint) => ({ chord: hint.key, desc: hint.description }));
}

/**
 * Desktop KeybindBar hints — live from {@link resolveBindings} + {@link selectBottomBar}.
 * Browser always delivers Ctrl, so `ctrlAvailable` is true (unlike a bare TTY without kitty).
 *
 * When `modeHints` is set (spawn/help/workflow/settings), the bar follows the TUI mode path:
 * nav trio + mode keys only.
 *
 * When stage/chat is home (`focusedId === null`) and `stageSurface` is set, stage scroll/goto
 * chords are merged into the chat globals (CHAT_FOCUS alone would omit them).
 */
export function desktopKeybindHints(
  modifier: SettingsModifier,
  overrides: Readonly<Partial<Record<ActionId, string>>> = {},
  focusedId: FocusablePanelId | null = null,
  focusedKeymap?: Keymap<string>,
  modeHints?: readonly ModeHint[] | null,
  stageSurface: StageHintSurface = null,
): readonly KeybindHint[] {
  const bindings = resolveBindings(modifier, true, overrides);

  if (modeHints != null) {
    return toHints(
      selectBottomBar(
        focusIdForBottomBar(focusedId),
        undefined,
        bindings,
        modeHints.map((h) => ({ key: h.chord, description: h.desc })),
      ),
    );
  }

  if (focusedId === null && stageSurface !== null) {
    const stageKeymap =
      stageSurface === 'doc' ? WEB_STAGE_DOC_KEYMAP : WEB_STAGE_TRANSCRIPT_KEYMAP;
    const chatHints = selectBottomBar(CHAT_FOCUS, undefined, bindings);
    const stagePanelHints = stageKeymap
      .filter((entry) => entry.hidden !== true)
      .map((entry) => ({
        key: chordLabel(Array.isArray(entry.chord) ? entry.chord[0] : entry.chord),
        description: entry.description,
      }));
    const help = chatHints.filter((h) => h.align === 'right');
    const left = chatHints.filter((h) => h.align !== 'right');
    return toHints([...left, ...stagePanelHints, ...help]);
  }

  const focused = focusIdForBottomBar(focusedId);
  const keymap = focusedKeymap ?? keymapForFocusedPanel(focusedId);
  return toHints(selectBottomBar(focused, keymap, bindings));
}
