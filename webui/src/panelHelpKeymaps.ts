/**
 * Static panel keymap declarations for WebUI help + context-adaptive KeybindBar hints.
 * Mirrors what rail panels actually consume via {@link usePanelListKeys} (and TUI-aligned
 * doc/tree conventions). Not a live dispatcher registry — help/bar read-only.
 */

import { createKeymapRegistry, type KeymapRegistryApi } from '@murder/ui-core/input/keymapRegistry.js';
import type { Keymap, PanelKeymap } from '@murder/ui-core/input/keymap.js';
import type { PanelId } from '@murder/ui-core/input/panels.js';
import type { FocusablePanelId } from './panelFocus.js';

function helpKeymap(entries: Keymap<string>): PanelKeymap {
  return { keymap: entries, onIntent: () => {} };
}

const LIST_NAV: Keymap<string> = [
  { chord: [{ input: 'j' }, { key: { downArrow: true } }], intent: 'down', description: 'next' },
  { chord: [{ input: 'k' }, { key: { upArrow: true } }], intent: 'up', description: 'prev' },
];

/** Declared keymaps keyed by rail panel id (web surface). */
export const WEB_PANEL_KEYMAPS: Readonly<Partial<Record<PanelId, Keymap<string>>>> = {
  crows: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next crow' }
        : { ...e, description: 'prev crow' },
    ),
    { chord: { key: { return: true } }, intent: 'open', description: 'toggle transcript pane' },
    { chord: { input: 'f' }, intent: 'star', description: 'favorite' },
    { chord: { input: 'm' }, intent: 'meta', description: 'toggle meta density' },
    { chord: { input: 'x' }, intent: 'murder', description: 'murder' },
  ],
  history: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next item' }
        : { ...e, description: 'prev item' },
    ),
    { chord: { key: { return: true } }, intent: 'resume', description: 'resume' },
    { chord: { input: 'r' }, intent: 'resumeOrRefresh', description: 'resume / refresh' },
    { chord: { input: 'a' }, intent: 'toggleMode', description: 'loose ↔ all' },
    { chord: { input: 'x' }, intent: 'dismiss', description: 'dismiss' },
  ],
  usage: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next gauge' }
        : { ...e, description: 'prev gauge' },
    ),
    { chord: { input: 'r' }, intent: 'refresh', description: 'sample' },
  ],
  workflows: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down' ? { ...e, description: 'next row' } : { ...e, description: 'prev row' },
    ),
    { chord: { key: { return: true } }, intent: 'open', description: 'open / toggle' },
    { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
  ],
  plans: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next plan' }
        : { ...e, description: 'prev plan' },
    ),
    { chord: { key: { return: true } }, intent: 'open', description: 'view / create' },
    { chord: { input: 'f' }, intent: 'star', description: 'favorite' },
  ],
  notes: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next note' }
        : { ...e, description: 'prev note' },
    ),
    { chord: { key: { return: true } }, intent: 'open', description: 'view / create' },
    { chord: { input: 'f' }, intent: 'star', description: 'favorite' },
  ],
  reports: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next report' }
        : { ...e, description: 'prev report' },
    ),
    { chord: { key: { return: true } }, intent: 'open', description: 'view / create' },
    { chord: { input: 'f' }, intent: 'star', description: 'favorite' },
  ],
  tree: [
    ...LIST_NAV.map((e) =>
      e.intent === 'down'
        ? { ...e, description: 'next lane' }
        : { ...e, description: 'prev lane' },
    ),
    {
      chord: [{ input: 'h' }, { key: { leftArrow: true } }],
      intent: 'older',
      description: 'older',
    },
    {
      chord: [{ input: 'l' }, { key: { rightArrow: true } }],
      intent: 'newer',
      description: 'newer',
    },
    { chord: { input: 'g' }, intent: 'startG', description: 'jump (g)' },
  ],
};

/** Stage document scroll / page / goto (DocViewer; help + stage-focused bar). */
export const WEB_STAGE_DOC_KEYMAP: Keymap<string> = [
  {
    chord: [{ input: 'j' }, { key: { downArrow: true } }],
    intent: 'scrollDown',
    description: 'scroll down',
  },
  {
    chord: [{ input: 'k' }, { key: { upArrow: true } }],
    intent: 'scrollUp',
    description: 'scroll up',
  },
  {
    chord: [{ input: ' ' }, { key: { pageDown: true } }],
    intent: 'pageDown',
    description: 'page down',
  },
  {
    chord: [{ input: 'b' }, { key: { pageUp: true } }],
    intent: 'pageUp',
    description: 'page up',
  },
  { chord: { input: 'g' }, intent: 'goto.start', description: 'go to line' },
];

/** Stage transcript turn scroll / goto (ChatTranscript; help + stage-focused bar). */
export const WEB_STAGE_TRANSCRIPT_KEYMAP: Keymap<string> = [
  {
    chord: [{ input: 'j' }, { key: { downArrow: true } }],
    intent: 'scrollDown',
    description: 'newer',
  },
  {
    chord: [{ input: 'k' }, { key: { upArrow: true } }],
    intent: 'scrollUp',
    description: 'older',
  },
  { chord: { input: 'g' }, intent: 'goto.start', description: 'go to line' },
];

/** Build a keymap registry pre-filled with {@link WEB_PANEL_KEYMAPS} for help groups. */
export function createWebHelpKeymapRegistry(): KeymapRegistryApi {
  const registry = createKeymapRegistry();
  for (const [id, keymap] of Object.entries(WEB_PANEL_KEYMAPS) as [
    PanelId,
    Keymap<string>,
  ][]) {
    registry.getState().register(id, helpKeymap(keymap));
  }
  return registry;
}

/** Keymap entries for the focused rail panel (or undefined for stage/chat/settings). */
export function keymapForFocusedPanel(
  focusedId: FocusablePanelId | null,
): Keymap<string> | undefined {
  if (focusedId === null || focusedId === 'settings') return undefined;
  return WEB_PANEL_KEYMAPS[focusedId];
}
