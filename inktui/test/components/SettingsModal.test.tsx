/**
 * SettingsModal tests — the `alt+o` / `ctrl+o` settings menu against the C7M modal idiom.
 *
 * Coverage:
 *  1. Opens / paints the three sections / Esc dismisses + restores focus.
 *  2. The settings chord (`alt+o`) opens the modal end-to-end through the dispatcher.
 *  3. Modifier radio: selecting `alt` commits via `update`; ctrl/both disabled + notice when kitty
 *     is unsupported (and the disabled rows refuse selection).
 *  4. Theme select: cursor-move live-previews within the theme rows; leaving the section / Esc
 *     reverts to the persisted theme.
 *  5. Theme commit on Enter persists via `update` and remains active after leaving the section.
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

async function openCategory(
  stdin: { write: (s: string) => void },
  lastFrame: () => string | undefined,
  label: string,
): Promise<void> {
  for (let i = 0; i < 10; i++) {
    const focusedLine = (lastFrame() ?? '').split('\n').find((l) => l.includes('›'));
    if (focusedLine?.includes(label)) {
      stdin.write('l');
      await tick();
      return;
    }
    stdin.write('j');
    await tick();
  }
  throw new Error(`never focused category "${label}"`);
}

/** A `current` with the extended harness + llm data populated, for the new-section tests. */
const RICH_CURRENT: Parameters<typeof settingsMode>[2] = {
  modifier: 'alt',
  theme: DEFAULT_THEME_ID,
  paneGap: 0,
  keyOverrides: {},
  collaboratorHarness: null,
  effectiveCollaborator: 'claude_code',
  plannerHarness: null,
  effectivePlanner: 'claude_code',
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
    expect(frame).toContain('Appearance');
    expect(frame).toContain('Harnesses');
    expect(frame).toContain('LLM');
    expect(frame).toContain('Templates');
    expect(frame).toContain('Keybindings');
    expect(frame).toContain('Theme');
    expect(frame).toContain('Pane Gap');
    expect(selectActiveMode(stores.modes)?.id).toBe(SETTINGS_MODE_ID);

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
    expect(stores.focus.getState().intendedId).toBe('notes');
  });

  it('category cursor moves with j/k', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('Appearance');
    stdin.write('j');
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('Harnesses');
    stdin.write('k');
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('Appearance');
  });

  it('l/Enter enter settings rows and h returns to categories', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('l');
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain(
      'Everforest Dark',
    );
    stdin.write('h');
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('Appearance');
    stdin.write('\r');
    await tick();
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain(
      'Everforest Dark',
    );
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
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    await openCategory(stdin, lastFrame, 'Keybindings');
    // Cursor starts on the first modifier row (`alt`). Enter selects it.
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ modifier: 'alt' });
  });

  it('selecting a pane-gap option commits via update', async () => {
    // Start at gap 0; navigate to the second gap row (value 1) and Enter → update({ pane_gap: 1 }).
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('l');
    await tick();
    await walkUntilFocused(stdin, lastFrame, '1  │');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ pane_gap: 1 });
  });

  it('selecting the vim-mode "on" row commits update({ vim_mode: true })', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    await openCategory(stdin, lastFrame, 'Keybindings');
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
    await openCategory(stdin, lastFrame, 'Keybindings');
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

  it('moving onto a theme row previews; leaving the theme section reverts', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('l');
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);

    const other: ThemeId = 'everforest-light';
    await walkUntilFocused(stdin, lastFrame, 'Everforest Light');
    expect(themeStore.getState().id).toBe(other);
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('( )');

    await walkUntilFocused(stdin, lastFrame, '0 (flush)');
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);
  });

  it('Esc while previewing a theme reverts to the persisted theme', async () => {
    const { stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('l');
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);

    const other: ThemeId = 'everforest-light';
    await walkUntilFocused(stdin, lastFrame, 'Everforest Light');
    expect(themeStore.getState().id).toBe(other);

    stdin.write(ESC);
    await tick();
    expect(themeStore.getState().id).toBe(DEFAULT_THEME_ID);
  });

  it('Enter on a theme row commits the previewed theme via update', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    stdin.write('l');
    await tick();
    const other: ThemeId = 'everforest-light';
    await walkUntilFocused(stdin, lastFrame, 'Everforest Light');
    expect(themeStore.getState().id).toBe(other);
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ theme: other });
    expect(themeStore.getState().id).toBe(other);
    expect((lastFrame() ?? '').split('\n').find((l) => l.includes('›'))).toContain('(•)');

    await walkUntilFocused(stdin, lastFrame, '0 (flush)');
    expect(themeStore.getState().id).toBe(other);
  });

  it('rebinds a key: Enter captures, a clean char commits via update', async () => {
    const { stores, patches, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    enter();
    await tick();
    await openCategory(stdin, lastFrame, 'Keybindings');
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
    await openCategory(stdin, lastFrame, 'Keybindings');
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
    await openCategory(stdin, lastFrame, 'Keybindings');
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

  it('does not show the dormant collaborator harness section', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await openCategory(stdin, lastFrame, 'Harnesses');
    await walkUntilFocused(stdin, lastFrame, '(default)');
    expect(lastFrame()).toContain('Planning Agent Harness');
    expect(lastFrame()).toContain('Claude Code');
    expect(lastFrame()).not.toContain('Collaborator harness');
  });

  it('planner radio: selecting a harness commits planner_harness', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await openCategory(stdin, lastFrame, 'Harnesses');
    // A "codex" harness row also exists in the Startup Rogue section (above planner); walk past it
    // by first focusing the planner "(default)" row, then the planner codex row.
    await walkUntilFocused(stdin, lastFrame, '(default)');
    await walkUntilFocused(stdin, lastFrame, 'Codex');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ planner_harness: 'codex' });
  });

  it('planner "(default)" row commits planner_harness: null', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // Start with an override set, so selecting "(default)" is an observable clear.
    stores.modes
      .getState()
      .enter(settingsMode(stores.modes, actions, { ...RICH_CURRENT, plannerHarness: 'codex' }));
    await tick();
    await openCategory(stdin, lastFrame, 'Harnesses');
    await walkUntilFocused(stdin, lastFrame, '(default)');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ planner_harness: null });
  });

  it('crow checkbox: toggling a harness commits the crow_harnesses list', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    // Effective default is [claude_code]; toggling codex on yields [claude_code, codex].
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await openCategory(stdin, lastFrame, 'Harnesses');
    // Two harnesses named "codex" exist (planner + crow); walk past the planner one by first
    // focusing the crow reset row, then the crow codex row.
    await walkUntilFocused(stdin, lastFrame, 'reset to default');
    await walkUntilFocused(stdin, lastFrame, 'Codex'); // now the crow Codex row
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
    await openCategory(stdin, lastFrame, 'Harnesses');
    await walkUntilFocused(stdin, lastFrame, 'reset to default');
    await walkUntilFocused(stdin, lastFrame, 'Codex'); // the crow Codex row (checked)
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
    await openCategory(stdin, lastFrame, 'LLM');
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
    await openCategory(stdin, lastFrame, 'LLM');
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
    await openCategory(stdin, lastFrame, 'LLM');
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
    await openCategory(stdin, lastFrame, 'LLM');
    // Scroll down toward the tiers section so the built-ins are in view.
    await walkUntilFocused(stdin, lastFrame, 'local base_url');
    const frame = lastFrame() ?? '';
    expect(frame).toContain('cheap');
    expect(frame).toContain('smart');
    // The smart built-in is cerebras/openai/gpt-oss-120b.
    expect(frame).toContain('openai/gpt-oss-120b');
  });

  it('role radio: selecting a tier commits llm.roles.<role>', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions, patches } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, RICH_CURRENT));
    await tick();
    await openCategory(stdin, lastFrame, 'LLM');
    // Role rows render as "<role>: <tier>"; focus notetaker: smart and Enter.
    await walkUntilFocused(stdin, lastFrame, 'notetaker: smart');
    stdin.write('\r');
    await tick();
    expect(patches).toContainEqual({ llm: { roles: { notetaker: 'smart' } } });
  });

  // --- Templates section ---

  /** A spy templates handle recording rename/remove calls. */
  function fakeTemplateActions(): {
    handle: {
      remove(name: string): void;
      rename(oldName: string, newName: string): void;
      save(name: string, body: string): void;
    };
    removed: string[];
    renamed: Array<[string, string]>;
    saved: Array<[string, string]>;
  } {
    const removed: string[] = [];
    const renamed: Array<[string, string]> = [];
    const saved: Array<[string, string]> = [];
    const handle = {
      remove: (name: string) => removed.push(name),
      rename: (oldName: string, newName: string) => renamed.push([oldName, newName]),
      save: (name: string, body: string) => saved.push([name, body]),
    };
    return { handle, removed, renamed, saved };
  }

  function templatesCurrent(
    items: ReadonlyArray<{ name: string; body: string }>,
    handle?: {
      remove(name: string): void;
      rename(oldName: string, newName: string): void;
      save(name: string, body: string): void;
    },
  ): Parameters<typeof settingsMode>[2] {
    return {
      ...RICH_CURRENT,
      templates: items,
      ...(handle !== undefined ? { templateActions: handle } : {}),
    };
  }

  it('renders a Templates header + one row per template', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      settingsMode(
        stores.modes,
        actions,
        templatesCurrent([
          { name: 'greet', body: 'hello' },
          { name: 'bye', body: 'goodbye' },
        ]),
      ),
    );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    await walkUntilFocused(stdin, lastFrame, ':greet');
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Templates');
    expect(frame).toContain(':greet');
    expect(frame).toContain(':bye');
  });

  it('shows the empty-state hint when there are no templates', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(settingsMode(stores.modes, actions, templatesCurrent([])));
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    expect(lastFrame()).toContain('no templates');
  });

  it('creates a template with inline name then body entry', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, saved } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(settingsMode(stores.modes, actions, templatesCurrent([], handle)));
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    await walkUntilFocused(stdin, lastFrame, 'New Template');
    stdin.write('\r');
    await tick();
    stdin.write('n');
    stdin.write('e');
    stdin.write('w');
    await tick();
    stdin.write('\r');
    await tick();
    stdin.write('b');
    stdin.write('o');
    stdin.write('d');
    stdin.write('y');
    await tick();
    stdin.write('\r');
    await tick();
    expect(saved).toContainEqual(['new', 'body']);
  });

  it('previews the template body when the cursor lands on its row', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(
        settingsMode(
          stores.modes,
          actions,
          templatesCurrent([{ name: 'greet', body: 'hello world body' }]),
        ),
      );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':greet');
    const frame = lastFrame() ?? '';
    expect(frame).toContain('preview');
    expect(frame).toContain('hello world body');
  });

  it('Enter on a template begins a rename; a clean new name calls rename()', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, renamed } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(
        settingsMode(stores.modes, actions, templatesCurrent([{ name: 'old', body: 'b' }], handle)),
      );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':old');
    stdin.write('\r'); // begin rename (buffer seeded with "old")
    await tick();
    // Clear the seeded name and type a new one.
    stdin.write('\x15'); // meta+u clears — but use deleteAll; the keymap binds meta+u. Fall back to backspaces.
    await tick();
    // Robust clear: three backspaces remove "old", then type "new".
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    await tick();
    stdin.write('n');
    stdin.write('e');
    stdin.write('w');
    await tick();
    stdin.write('\r'); // commit
    await tick();
    expect(renamed).toContainEqual(['old', 'new']);
  });

  it('rename rejects an invalid name with a notice and does not call rename()', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, renamed } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(
        settingsMode(stores.modes, actions, templatesCurrent([{ name: 'old', body: 'b' }], handle)),
      );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':old');
    stdin.write('\r');
    await tick();
    // Append "!" → "old!" is invalid (`!` not in [A-Za-z0-9_-]).
    stdin.write('!');
    await tick();
    stdin.write('\r'); // commit attempt
    await tick();
    expect(lastFrame()).toContain('invalid');
    expect(renamed).toHaveLength(0);
  });

  it('rename rejects a collision with an existing template name', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, renamed } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      settingsMode(
        stores.modes,
        actions,
        templatesCurrent(
          [
            { name: 'aaa', body: 'x' },
            { name: 'bbb', body: 'y' },
          ],
          handle,
        ),
      ),
    );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':aaa');
    stdin.write('\r'); // rename "aaa", buffer = "aaa"
    await tick();
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    await tick();
    stdin.write('b');
    stdin.write('b');
    stdin.write('b'); // collides with the other template
    await tick();
    stdin.write('\r');
    await tick();
    expect(lastFrame()).toContain('already exists');
    expect(renamed).toHaveLength(0);
  });

  it('d on a template prompts a confirm; y deletes via remove()', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, removed } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(
        settingsMode(
          stores.modes,
          actions,
          templatesCurrent([{ name: 'gone', body: 'b' }], handle),
        ),
      );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':gone');
    stdin.write('d'); // open the confirm
    await tick();
    expect(lastFrame()).toContain('(y/n)');
    stdin.write('y'); // confirm delete
    await tick();
    expect(removed).toContainEqual('gone');
  });

  it('d-confirm cancels on n without deleting', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { actions } = fakeActions();
    const { handle, removed } = fakeTemplateActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes
      .getState()
      .enter(
        settingsMode(
          stores.modes,
          actions,
          templatesCurrent([{ name: 'stay', body: 'b' }], handle),
        ),
      );
    await tick();
    await openCategory(stdin, lastFrame, 'Templates');
    stdin.write('j');
    await tick();
    await walkUntilFocused(stdin, lastFrame, ':stay');
    stdin.write('d');
    await tick();
    expect(lastFrame()).toContain('(y/n)');
    stdin.write('n'); // cancel
    await tick();
    expect(removed).toHaveLength(0);
    // The confirm prompt is gone.
    expect(lastFrame()).not.toContain('(y/n)');
  });
});
