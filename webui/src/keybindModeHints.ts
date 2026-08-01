/**
 * Mode-owned KeybindBar hints while a web dialog (or settings rail) is active.
 * Mirrors TUI {@link selectBottomBar}'s `modeHints` path — nav trio + mode keys, no panel/help.
 */

import { createStore } from 'zustand/vanilla';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { WizardStep } from '@murder/ui-core/components/spawnWizardMachine.js';
import type { KeybindHint } from './components/ds/index.js';

export type ModeHint = KeybindHint;

type ModeHintsState = {
  readonly hints: readonly ModeHint[] | null;
  set(hints: readonly ModeHint[]): void;
  clear(): void;
};

/** Session-local active-mode hints for the desktop KeybindBar. */
export const keybindModeHintsStore = createStore<ModeHintsState>()((set) => ({
  hints: null,
  set(hints) {
    set({ hints });
  },
  clear() {
    set({ hints: null });
  },
}));

/** Subscribe to the active mode hints (or null when no modal owns the bar). */
export function useKeybindModeHints(): readonly ModeHint[] | null {
  return useStoreWithEqualityFn(keybindModeHintsStore, (s) => s.hints);
}

/** Publish hints while mounted; clear on unmount. Re-publish when `hints` identity/content changes. */
export function publishModeHints(hints: readonly ModeHint[]): () => void {
  keybindModeHintsStore.getState().set(hints);
  return () => {
    keybindModeHintsStore.getState().clear();
  };
}

const CANCEL: ModeHint = { chord: 'esc', desc: 'cancel' };

/** Spawn wizard step hints (TUI `spawnWizardHints` parity, web chord labels). */
export function spawnDialogHints(
  step: WizardStep,
  ctx?: { readonly favoritesFocused?: boolean },
): readonly ModeHint[] {
  switch (step) {
    case 'harness':
      return [
        { chord: 'h/l', desc: 'cols' },
        { chord: 'j/k', desc: 'nav' },
        { chord: '[n]', desc: 'select' },
        { chord: 'enter', desc: 'confirm' },
        ...(ctx?.favoritesFocused === true
          ? [{ chord: 'd/r', desc: 'del/rename' } satisfies ModeHint]
          : []),
        CANCEL,
      ];
    case 'model':
    case 'effort':
    case 'worktree':
      return [
        { chord: 'j/k', desc: 'nav' },
        { chord: '[n]', desc: 'select' },
        { chord: 'enter', desc: 'confirm' },
        CANCEL,
      ];
    case 'branch':
    case 'name':
    case 'nameFavorite':
      return [{ chord: 'enter', desc: 'confirm' }, CANCEL];
    case 'context':
      return [
        { chord: 'h/l', desc: 'nav' },
        { chord: 'enter', desc: 'confirm' },
        { chord: 'y/n', desc: 'include/skip' },
        CANCEL,
      ];
    default:
      return [CANCEL];
  }
}

/** Help dialog bar hints. */
export function helpDialogHints(multiPage: boolean): readonly ModeHint[] {
  return multiPage
    ? [
        { chord: 'h/l ←→', desc: 'pages' },
        { chord: 'esc', desc: 'quit' },
      ]
    : [{ chord: 'esc', desc: 'quit' }];
}

/** Settings rail focus — form-ish browse (TUI settings mode is a modal; web is a panel). */
export const SETTINGS_PANEL_HINTS: readonly ModeHint[] = [
  { chord: 'tab', desc: 'fields' },
  { chord: '↑/↓', desc: 'lists' },
  { chord: 'enter', desc: 'toggle / edit' },
  { chord: 'esc', desc: 'blur' },
];

/** @deprecated Prefer {@link SETTINGS_PANEL_HINTS}. */
export function settingsPanelHints(): readonly ModeHint[] {
  return SETTINGS_PANEL_HINTS;
}

/** Workflow template library. */
export const WORKFLOW_LIBRARY_HINTS: readonly ModeHint[] = [
  { chord: 'j/k', desc: 'select' },
  { chord: 'enter', desc: 'run' },
  { chord: 'n', desc: 'new' },
  { chord: 'e', desc: 'edit' },
  { chord: '/', desc: 'filter' },
  { chord: 'esc', desc: 'close' },
];

/** Workflow launch review. */
export const WORKFLOW_LAUNCH_HINTS: readonly ModeHint[] = [
  { chord: 'tab', desc: 'next field' },
  { chord: 'enter', desc: 'launch' },
  { chord: 'esc', desc: 'back' },
];

/** Workflow graph editor (normal interaction). */
export const WORKFLOW_EDITOR_HINTS: readonly ModeHint[] = [
  { chord: '/', desc: 'find stage' },
  { chord: 'del', desc: 'delete' },
  { chord: 'C-z/y', desc: 'undo/redo' },
  { chord: 'esc', desc: 'close' },
];
