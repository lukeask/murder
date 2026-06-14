/**
 * SettingsModal tests — the `alt+o` / `ctrl+o` settings menu against the C7M modal idiom.
 *
 * Coverage:
 *  1. Opens / paints the three sections / Esc dismisses + restores focus.
 *  2. The settings chord (`alt+o`) opens the modal end-to-end through the dispatcher.
 *  3. Modifier radio: selecting `alt` commits via `update`; ctrl/both disabled + notice when kitty
 *     is unsupported (and the disabled rows refuse selection).
 *  4. Theme select: cursor-move live-previews (setTheme fires); Esc reverts to the persisted theme.
 *  5. Theme commit on Enter persists via `update`.
 *  6. Key rebind: Enter on a binding row captures the next key; a clean char commits via `update`;
 *     a reserved char (digit / ctrl-exit letter) and a collision are both rejected with a notice.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Overlay } from '../../src/components/Overlay.js';
import {
  SETTINGS_MODE_ID,
  type SettingsModeOptions,
  settingsMode,
} from '../../src/components/SettingsModal.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import type { SettingsActions, SettingsPatch } from '../../src/store/settings/settingsActions.js';
import { capsStore } from '../../src/terminal/capsStore.js';
import { DEFAULT_THEME_ID, type ThemeId } from '../../src/theme/palettes.js';
import { themeStore } from '../../src/theme/themeStore.js';

const ESC = '\x1b';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Walk the cursor down (j) until the first binding row ("spawn", the first rebindable action) is
 * focused. The list scrolls by cursor, so we step until that focused row is in the visible frame.
 * Robust to the section ordering above the bindings (harnesses/providers/tiers/roles). */
async function walkToFirstBinding(
  stdin: { write: (s: string) => void },
  lastFrame: () => string | undefined,
): Promise<void> {
  await walkUntilFocused(stdin, lastFrame, 'spawn');
}

/** Walk the cursor down (j) until the focused row (the line carrying the `›` cursor prefix) contains
 * `marker`. Robust to the scroll-by-cursor window and the radio/checkbox mark glyphs. */
async function walkUntilFocused(
  stdin: { write: (s: string) => void },
  lastFrame: () => string | undefined,
  marker: string,
): Promise<void> {
  for (let i = 0; i < 80; i++) {
    const focusedLine = (lastFrame() ?? '').split('\n').find((l) => l.includes('›'));
    if (focusedLine?.includes(marker)) {
      return;
    }
    stdin.write('j');
    await tick();
  }
  throw new Error(`never focused a row matching "${marker}"`);
}

/** A `current` with the extended harness + llm data populated, for the new-section tests. */
const RICH_CURRENT: Parameters<typeof settingsMode>[2] = {
  modifier: 'alt',
  theme: DEFAULT_THEME_ID,
  paneGap: 0,
  keyOverrides: {},
  collaboratorHarness: null,
  effectiveCollaborator: 'claude_code',
  crowHarnesses: null,
  effectiveCrow: ['claude_code'],
  llm: {},
  llmEnv: { groq: true, cerebras: false, openrouter: false },
};

/** A spy `SettingsActions` recording every `update` patch. `load` is unused by the modal. */
function fakeActions(): { actions: SettingsActions; patches: SettingsPatch[] } {
  const patches: SettingsPatch[] = [];
  const actions: SettingsActions = {
    load: vi.fn(async () => {}),
    update: vi.fn(async (patch: SettingsPatch) => {
      patches.push(patch);
    }),
  };
  return { actions, patches };
}

function RootInput({ openSettings }: { readonly openSettings?: () => void }): null {
  useRootInput({ ...(openSettings !== undefined ? { openSettings } : {}) });
  return null;
}

function Harness({
  stores,
  openSettings,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
  readonly openSettings?: () => void;
}): JSX.Element {
  return (
    <InputStoresProvider value={stores}>
      <RootInput {...(openSettings !== undefined ? { openSettings } : {})} />
      <Overlay />
    </InputStoresProvider>
  );
}

/** Build stores (notes focused), spy actions, and an `enter(current, opts)` opening the modal. */
function setup(
  current: Parameters<typeof settingsMode>[2] = {
    modifier: 'alt',
    theme: DEFAULT_THEME_ID,
    paneGap: 0,
    keyOverrides: {},
  },
) {
  const stores = createInputStores(['notes'], 'notes');
  const { actions, patches } = fakeActions();
  const enter = (opts: SettingsModeOptions = {}) =>
    stores.modes.getState().enter(settingsMode(stores.modes, actions, current, opts));
  return { stores, actions, patches, enter };
}

describe('SettingsModal', () => {
  beforeEach(() => {
    // Default to kitty-supported so ctrl/both are enabled unless a case says otherwise.
    capsStore.getState().setKittySupported(true);
    themeStore.getState().setTheme(DEFAULT_THEME_ID);
  });
  afterEach(() => {
    capsStore.getState().setKittySupported('detecting');
    themeStore.getState().setTheme(DEFAULT_THEME_ID);
  });

  it('opens, paints the top sections, Esc dismisses and restores focus', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    await tick();
    expect(lastFrame()).not.toContain('Settings');

    enter();
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Settings');
    expect(frame).toContain('Command modifier');
    expect(frame).toContain('Theme');
    expect(frame).toContain('Pane gap');
    // The row list now scrolls by cursor (it is far taller than the screen). The later sections —
    // Collaborator harness, LLM providers, Tiers, Role → tier, Key bindings — come into view as the
    // cursor descends; the top sections paint on open.
    expect(selectActiveMode(stores.modes)?.id).toBe(SETTINGS_MODE_ID);

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(stores.focus.getState().intendedId).toBe('notes');
  });

  it('the openSettings handler opens the modal (the chord→handler→modal wiring)', async () => {
    // The dispatcher routing of the `global.settings` chord → `openSettings` is proven in the
    // dispatcher suite. Here we prove the handler half: firing `openSettings` (as the shell wires it
    // to `settingsMode`) opens the modal and it paints.
    const { stores, enter } = setup();
    const open = (): void => enter();
    const { lastFrame } = render(<Harness stores={stores} openSettings={open} />);
    await tick();
    expect(lastFrame()).not.toContain('Settings');

    // Fire the handler the dispatcher would call on the settings chord.
    open();
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(SETTINGS_MODE_ID);
    expect(lastFrame()).toContain('Settings');
  });

  it('selecting a modifier commits via update', async () => {
    // Start on `both` so selecting `alt` is an observable change.
    const { stores, patches, enter } = setup({
      modifier: 'both',
      theme: DEFAULT_THEME_ID,
      paneGap: 0,
      keyOverrides: {},
    });
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // Cursor starts on the first modifier row (`alt`). Enter selects it.
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ modifier: 'alt' });
  });

  it('selecting a pane-gap option commits via update', async () => {
    // Start at gap 0; navigate to the second gap row (value 1) and Enter → update({ pane_gap: 1 }).
    const { stores, patches, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // Selectable rows: 3 modifiers + 2 themes precede the gap section; one more `j` lands on gap row 1.
    for (let i = 0; i < 6; i++) {
      stdin.write('j');
      await tick();
    }
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ pane_gap: 1 });
  });

  it('selecting the vim-mode "on" row commits update({ vim_mode: true })', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // The Vim mode radio sits right after Pane gap; walk down until the focused row is "on".
    await walkUntilFocused(stdin, lastFrame, 'on');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ vim_mode: true });
  });

  it('ctrl/both are disabled with a notice when kitty is unsupported', async () => {
    capsStore.getState().setKittySupported(false);
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('kitty keyboard protocol');
    expect(frame).toContain('unavailable');

    // Move down to the `ctrl` row and try to select it → no update (disabled), notice shown.
    stdin.write('j');
    await tick();
    stdin.write('\r');
    await tick();
    expect(patches.find((p) => p.modifier === 'ctrl')).toBeUndefined();
    // The notice is present (text wraps across lines, so match an unwrapped fragment).
    expect(lastFrame()).toContain('kitty keyboard protocol');
  });

  it('moving the cursor onto a theme row live-previews; Esc reverts', async () => {
    const { stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);

    // Navigate down to the second theme row (everforest-light). Rows: 3 modifiers + 1st theme is the
    // default; one more `j` lands on the alternate theme → preview fires.
    // Walk down until the theme id changes (robust to row counts).
    const other: ThemeId = 'everforest-light';
    for (let i = 0; i < 20 && themeStore.getState().id !== other; i++) {
      stdin.write('j');
      await tick();
    }
    expect(themeStore.getState().id).toBe(other);

    // Esc reverts the live preview to the persisted (default) theme.
    stdin.write(ESC);
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);
  });

  it('Enter on a theme row commits the previewed theme via update', async () => {
    const { stores, patches, enter } = setup();
    const { stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    const other: ThemeId = 'everforest-light';
    for (let i = 0; i < 20 && themeStore.getState().id !== other; i++) {
      stdin.write('j');
      await tick();
    }
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ theme: other });
  });

  it('rebinds a key: Enter captures, a clean char commits via update', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    // Walk to the first binding row ("spawn") — bindings are the last section, after the harness /
    // LLM / tier / role sections, and the list scrolls by cursor.
    await walkToFirstBinding(stdin, lastFrame);
    stdin.write('\r'); // begin capture
    await tick();
    expect(lastFrame()).toContain('press a key');

    stdin.write('q'); // a clean, unreserved, non-colliding char
    await tick();
    const rebind = patches.find((p) => p.key_overrides !== undefined);
    expect(rebind).toBeDefined();
    expect(Object.values(rebind?.key_overrides ?? {})).toContain('q');
  });

  it('rejects a reserved capture char (digit) with a notice', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    await walkToFirstBinding(stdin, lastFrame);
    stdin.write('\r'); // begin capture
    await tick();
    stdin.write('3'); // a reserved digit
    await tick();
    expect(lastFrame()).toContain('reserved');
    expect(patches.find((p) => p.key_overrides !== undefined)).toBeUndefined();
  });

  it('rejects a colliding capture char with a notice naming the other action', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    await walkToFirstBinding(stdin, lastFrame);
    stdin.write('\r'); // begin capture on the FIRST binding row
    await tick();
    // The first rebindable action defaults to 's' (spawn); bind the first row to another action's
    // default key to force a collision. The new-plan action defaults to 'p'.
    stdin.write('p');
    await tick();
    expect(lastFrame()).toContain('already bound');
    expect(patches.find((p) => p.key_overrides !== undefined)).toBeUndefined();
  });

  // --- Harnesses section ---

  it('collaborator radio: selecting a harness commits collaborator_harness', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await walkUntilFocused(stdin, lastFrame, 'codex');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ collaborator_harness: 'codex' });
  });

  it('collaborator "(default)" row commits collaborator_harness: null', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // Start with an override set, so selecting "(default)" is an observable clear.
    stores.modes
      .getState()
      .enter(
        settingsMode(stores.modes, actions, { ...RICH_CURRENT, collaboratorHarness: 'codex' }),
      );
    await tick();
    await walkUntilFocused(stdin, lastFrame, '(default)');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ collaborator_harness: null });
  });

  it('crow checkbox: toggling a harness commits the crow_harnesses list', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // Effective default is [claude_code]; toggling codex on yields [claude_code, codex].
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    // Two harnesses named "codex" exist (collaborator + crow); walk past the collaborator one by
    // first focusing the crow reset row, then the crow codex row.
    await walkUntilFocused(stdin, lastFrame, 'reset to default');
    await walkUntilFocused(stdin, lastFrame, 'codex'); // now the crow codex row
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ crow_harnesses: ['claude_code', 'codex'] });
  });

  it('crow checkbox: unchecking the last selected harness is blocked with a notice', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // Override = exactly [codex]; unchecking codex must be refused.
    stores.modes
      .getState()
      .enter(settingsMode(stores.modes, actions, { ...RICH_CURRENT, crowHarnesses: ['codex'] }));
    await tick();
    await walkUntilFocused(stdin, lastFrame, 'reset to default');
    await walkUntilFocused(stdin, lastFrame, 'codex'); // the crow codex row (checked)
    stdin.write('\r');
    await tick();
    expect(lastFrame()).toContain('At least one crow harness');
    expect(patches.find((p) => p.crow_harnesses !== undefined)).toBeUndefined();
  });

  // --- LLM providers section ---

  it('provider api_key: env-set provider shows "set via env"', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // groq has llm_env true → "set via env". Scroll the providers section into view first.
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await walkUntilFocused(stdin, lastFrame, 'groq api_key');
    expect(lastFrame()).toContain('set via env');
  });

  it('provider api_key: text-entry commits llm.providers.<p>.api_key', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // cerebras has no env key → editable. Focus its api_key row, type a key, Enter.
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await walkUntilFocused(stdin, lastFrame, 'cerebras api_key');
    stdin.write('\r'); // begin edit
    await tick();
    stdin.write('k');
    await tick();
    stdin.write('e');
    await tick();
    stdin.write('y');
    await tick();
    stdin.write('\r'); // commit
    await tick();
    expect(patches).toContainEqual({ llm: { providers: { cerebras: { api_key: 'key' } } } });
  });

  it('local provider has a base_url row and commits llm.providers.local.base_url', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await walkUntilFocused(stdin, lastFrame, 'local base_url');
    stdin.write('\r');
    await tick();
    stdin.write('u');
    await tick();
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ llm: { providers: { local: { base_url: 'u' } } } });
  });

  // --- Tiers & roles section ---

  it('tiers section lists the built-in cheap/smart tiers read-only', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    // Scroll down toward the tiers section so the built-ins are in view.
    await walkUntilFocused(stdin, lastFrame, 'local base_url');
    const frame = lastFrame() ?? '';
    expect(frame).toContain('cheap');
    expect(frame).toContain('smart');
    // The smart built-in is openrouter/anthropic/claude-sonnet-4-6.
    expect(frame).toContain('anthropic/claude-sonnet-4-6');
  });

  it('role radio: selecting a tier commits llm.roles.<role>', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    // Role rows render as "<role>: <tier>"; focus notetaker: smart and Enter.
    await walkUntilFocused(stdin, lastFrame, 'notetaker: smart');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ llm: { roles: { notetaker: 'smart' } } });
  });
});
