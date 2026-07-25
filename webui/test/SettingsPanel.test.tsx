/**
 * SettingsPanel theme switch + harness settings: clicking a theme swatch calls setTheme on the shared
 * themeStore (via useThemeCssVars) and repaints `--color-*` on :root. Harness controls persist via
 * `settings.update` with snake_case wire keys (planner / crow / control backends).
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { getPalette } from '@core/theme/palettes.js';
import { setTheme } from '@core/theme/themeStore.js';
import type { FakeApplicationClient } from '@core/application/FakeApplicationClient.js';
import type { AppStoreApi } from '@core/store/store.js';
import { fireEvent, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { useThemeCssVars } from '../src/theme/useThemeCssVars.js';
import { makeStore, renderWithStore } from './helpers.js';

function Harness(): React.JSX.Element {
  useThemeCssVars();
  return <SettingsPanel />;
}

/** Echo settings.update with current slice state + the patched keys from the request. */
function stubSettingsUpdate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('settings.update', (params) => {
    const s = store.getState().settings;
    const patch = params.settings as Record<string, unknown>;
    return {
      ok: true,
      settings: {
        theme: s.theme,
        modifier: s.modifier,
        key_overrides: s.keyOverrides,
        pane_gap: s.paneGap,
        vim_mode: s.vimMode,
        default_chat_view_mode: s.defaultChatViewMode,
        document_display_mode: s.documentDisplayMode,
        workspace_count: s.workspaceCount,
        bar_widgets: s.barWidgets,
        codex_control_backend: s.codexControlBackend,
        cursor_control_backend: s.cursorControlBackend,
        claude_control_backend: s.claudeControlBackend,
        startup_rogue: s.startupRogue,
        startup_rogue_models: s.startupRogueModels,
        startup_rogue_efforts: s.startupRogueEfforts,
        collaborator_harness: s.collaboratorHarness,
        planner_harness: s.plannerHarness,
        crow_harnesses: s.crowHarnesses,
        effective_collaborator_harness: s.effectiveCollaboratorHarness,
        effective_planner_harness: s.effectivePlannerHarness,
        effective_crow_harnesses: s.effectiveCrowHarnesses,
        llm: s.llm,
        llm_env: s.llmEnv,
        ...patch,
      },
    };
  });
}

afterEach(() => {
  cleanup();
  setTheme('everforest-dark');
});

describe('SettingsPanel theme switch', () => {
  it('repaints :root CSS vars when a theme is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    const lightPalette = getPalette('everforest-light')!;
    const lightText = buildTheme(lightPalette, 'light').text;
    fireEvent.click(screen.getByText('Everforest Light'));

    expect(document.documentElement.style.getPropertyValue('--color-text')).toBe(lightText);
  });

  it('reflects the active scheme onto <html data-theme> so DS components switch', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    expect(document.documentElement.dataset['theme']).toBe('dark');

    fireEvent.click(screen.getByText('Everforest Light'));
    expect(document.documentElement.dataset['theme']).toBe('light');
  });

  it('marks the active theme swatch', () => {
    setTheme('everforest-light');
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });
    const swatch = screen.getByText('Everforest Light').closest('.theme-swatch');
    expect(swatch?.getAttribute('data-on')).toBe('true');
  });

  it('persists a concrete model when startup rogue cursor is selected', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('startup rogue'), { target: { value: 'cursor' } });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: {
        settings: {
          startup_rogue: { harness: 'cursor', model: 'composer-2.5', effort: 'slow' },
        },
      },
    });
  });
});

describe('SettingsPanel harness settings', () => {
  it('persists planner_harness when a planner harness is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('planner harness'), { target: { value: 'codex' } });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { planner_harness: 'codex' } },
    });
  });

  it('clears planner_harness when default is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    store.setState({
      settings: { ...store.getState().settings, plannerHarness: 'codex' },
    });
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('planner harness'), { target: { value: '' } });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { planner_harness: null } },
    });
  });

  it('persists crow_harnesses when a harness checkbox is toggled', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('checkbox', { name: 'codex' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { crow_harnesses: ['claude_code', 'codex'] } },
    });
  });

  it('clears crow_harnesses when use default pool is enabled', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    store.setState({
      settings: { ...store.getState().settings, crowHarnesses: ['codex', 'pi'] },
    });
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('switch', { name: 'use default pool' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { crow_harnesses: null } },
    });
  });

  it('persists codex_control_backend when a backend is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('radio', { name: 'app_server' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { codex_control_backend: 'app_server' } },
    });
  });
});
