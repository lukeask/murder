/**
 * Shared keybinding-help groups — pure view-model from resolved bindings + keymap registry.
 * Consumed by the TUI HelpOverlay and the WebUI HelpDialog so labels track modifier + rebinds.
 */

import { ACTION_IDS, ACTIONS, chordLabel, type ResolvedBindings } from '../input/bindings.js';
import type { KeymapRegistryApi } from '../input/keymapRegistry.js';
import { PANELS } from '../input/panels.js';

/** One displayed binding row: the chord label and what it does. */
export interface HelpEntry {
  readonly key: string;
  readonly description: string;
}

/** A scope group of help entries, with a heading. */
export interface HelpGroup {
  readonly title: string;
  readonly entries: readonly HelpEntry[];
}

/** Human display label for one panel's scope heading. */
const PANEL_TITLE: Readonly<Record<string, string>> = {
  plans: 'Plans panel',
  notes: 'Notes panel',
  reports: 'Reports panel',
  workflows: 'Workflows panel',
  history: 'History panel',
  usage: 'Usage panel',
  tree: 'Tree panel',
  crows: 'Crows panel',
};

/**
 * Build the grouped help entries from the live bindings + keymap registry (pure — no React). The
 * `global` group reads the resolved bindings so the labels track the modifier + rebinds; the per-panel
 * groups read the registry's declared keymaps (only the *registered* panels appear, in {@link PANELS}
 * order); the `mode` group is the static convention shared by the modal surfaces.
 */
export function buildHelpGroups(
  bindings: ResolvedBindings,
  registry: KeymapRegistryApi,
): readonly HelpGroup[] {
  const groups: HelpGroup[] = [];

  // Global scope — every named action, labelled from the resolved bindings.
  groups.push({
    title: 'Global',
    entries: [
      {
        // The modifier prefix(es) (`A-`, `C-`, or `A-/C-`), derived by stripping the key name from
        // the focusChat label so the digit row tracks the user's modifier choice.
        key: `${bindings.label('global.focusChat').replaceAll('space', '')}1–0`,
        description: 'toggle/focus panels',
      },
      { key: 'h/j/k/l', description: 'panel nav (with command modifier)' },
      ...ACTION_IDS.filter((id) => id.startsWith('global.')).map((id) => ({
        key: bindings.label(id),
        description: ACTIONS[id].description,
      })),
      ...ACTION_IDS.filter((id) => id.startsWith('workspace.')).map((id) => ({
        key: bindings.label(id),
        description: ACTIONS[id].description,
      })),
    ],
  });

  // Panel scope — each registered panel's declared keymap.
  const keymaps = registry.getState().keymaps;
  for (const panel of PANELS) {
    const keymap = keymaps[panel.id]?.keymap;
    if (keymap === undefined || keymap.length === 0) {
      continue;
    }
    groups.push({
      title: PANEL_TITLE[panel.id] ?? `${panel.id} panel`,
      entries: keymap.map((entry) => ({
        key: chordLabel(Array.isArray(entry.chord) ? entry.chord[0] : entry.chord),
        description: entry.description,
      })),
    });
  }

  // Command scope — the chat-input prefix dispatcher's surface. Documented statically.
  groups.push({
    title: 'Commands',
    entries: [
      { key: '/…', description: 'passthrough to harness' },
      { key: ':help', description: 'this overlay' },
      { key: ':ticket', description: 'new ticket' },
      { key: ':note <text>', description: 'quick note' },
      { key: ':workflows', description: 'workflow library' },
      { key: ':rename <new>', description: 'rename rogue crow or plan' },
      { key: ':rename <old> <new>', description: 'rename a named plan' },
      { key: ':verbose / :compact / :tmux', description: 'set this pane view' },
      { key: ':dismiss-toasts', description: 'flush the toast rack' },
      { key: ':resume', description: 'use r in history panel' },
    ],
  });

  // Mode scope — conventions shared by the modal surfaces.
  groups.push({
    title: 'Modals',
    entries: [
      { key: 'j/k', description: 'navigate' },
      { key: '←/→ h/l', description: 'move / page' },
      { key: 'enter', description: 'confirm' },
      { key: 'esc', description: 'cancel / close' },
    ],
  });

  return groups;
}
