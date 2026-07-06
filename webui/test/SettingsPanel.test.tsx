/**
 * SettingsPanel theme switch: clicking a theme swatch calls setTheme on the shared themeStore, which
 * (via useThemeCssVars, mounted here) repaints the `--color-*` CSS variables on :root. We mount the
 * theme bridge alongside the panel, click the light theme, and assert a known CSS var changed to the
 * light palette's value. Also confirms the swatch reflects the active id.
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { getPalette } from '@core/theme/palettes.js';
import { setTheme } from '@core/theme/themeStore.js';
import { fireEvent, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { useThemeCssVars } from '../src/theme/useThemeCssVars.js';
import { makeStore, renderWithStore } from './helpers.js';

function Harness(): React.JSX.Element {
  useThemeCssVars();
  return <SettingsPanel />;
}

afterEach(() => {
  cleanup();
  setTheme('everforest-dark');
});

describe('SettingsPanel theme switch', () => {
  it('repaints :root CSS vars when a theme is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    const lightPalette = getPalette('everforest-light')!;
    const lightText = buildTheme(lightPalette, 'light').text;
    fireEvent.click(screen.getByText('Everforest Light'));

    expect(document.documentElement.style.getPropertyValue('--color-text')).toBe(lightText);
  });

  it('reflects the active scheme onto <html data-theme> so DS components switch', () => {
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    expect(document.documentElement.dataset['theme']).toBe('dark');

    fireEvent.click(screen.getByText('Everforest Light'));
    expect(document.documentElement.dataset['theme']).toBe('light');
  });

  it('marks the active theme swatch', () => {
    setTheme('everforest-light');
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });
    const swatch = screen.getByText('Everforest Light').closest('.theme-swatch');
    expect(swatch?.getAttribute('data-on')).toBe('true');
  });

  it('persists a concrete model when startup rogue cursor is selected', () => {
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    bus.stubRpc('settings.update', (params) => ({
      ok: true,
      settings: {
        theme: store.getState().settings.theme,
        modifier: store.getState().settings.modifier,
        key_overrides: store.getState().settings.keyOverrides,
        pane_gap: store.getState().settings.paneGap,
        vim_mode: store.getState().settings.vimMode,
        default_chat_view_mode: store.getState().settings.defaultChatViewMode,
        startup_rogue: params.settings.startup_rogue ?? null,
        startup_rogue_models: store.getState().settings.startupRogueModels,
        startup_rogue_efforts: store.getState().settings.startupRogueEfforts,
        collaborator_harness: store.getState().settings.collaboratorHarness,
        planner_harness: store.getState().settings.plannerHarness,
        crow_harnesses: store.getState().settings.crowHarnesses,
        effective_collaborator_harness: store.getState().settings.effectiveCollaboratorHarness,
        effective_planner_harness: store.getState().settings.effectivePlannerHarness,
        effective_crow_harnesses: store.getState().settings.effectiveCrowHarnesses,
        llm: store.getState().settings.llm,
        llm_env: store.getState().settings.llmEnv,
      },
    }));
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('startup rogue'), { target: { value: 'cursor' } });

    expect(bus.rpcCalls.at(-1)).toEqual({
      method: 'settings.update',
      params: {
        settings: {
          startup_rogue: { harness: 'cursor', model: 'composer-2.5', effort: 'slow' },
        },
      },
    });
  });
});
