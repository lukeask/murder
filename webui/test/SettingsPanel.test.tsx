/**
 * SettingsPanel theme switch + harness settings: clicking a theme swatch calls setTheme on the shared
 * themeStore (via useThemeCssVars) and repaints `--color-*` on :root. Harness controls persist via
 * `settings.update` with snake_case wire keys (planner / crow / control backends). Wave C1/C2 also
 * covers appearance extras, keybinding overrides, workspaces, bars, and LLM providers / feature policies.
 */

import { buildTheme } from '@murder/ui-core/theme/buildTheme.js';
import { getPalette } from '@murder/ui-core/theme/palettes.js';
import { setTheme } from '@murder/ui-core/theme/themeStore.js';
import type { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import type { AppStoreApi } from '@murder/ui-core/store/store.js';
import type { LlmWire, SettingsWire } from '@murder/ui-core/store/settings/settingsActions.js';
import { fireEvent, screen, cleanup, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { useThemeCssVars } from '../src/theme/useThemeCssVars.js';
import { makeStore, renderWithStore } from './helpers.js';

function Harness(): React.JSX.Element {
  useThemeCssVars();
  return <SettingsPanel />;
}

/** Build a full settings wire reply from the current slice, optionally overlaying `llm`. */
function settingsWire(store: AppStoreApi, llmOverride?: LlmWire): SettingsWire {
  const s = store.getState().settings;
  return {
    theme: s.theme,
    modifier: s.modifier,
    key_overrides: s.keyOverrides,
    pane_gap: s.paneGap,
    background_transparency: s.backgroundTransparency,
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
    llm: llmOverride ?? s.llm,
    llm_env: s.llmEnv,
  };
}

/** Echo settings.update with current slice state + the patched keys from the request. */
function stubSettingsUpdate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('settings.update', (params) => {
    const patch = params.settings as Record<string, unknown>;
    const base = settingsWire(store);
    const llmPatch = patch['llm'] as LlmWire | undefined;
    const llm =
      llmPatch === undefined
        ? base.llm
        : {
            ...base.llm,
            ...llmPatch,
            feature_policies: {
              ...(base.llm.feature_policies ?? {}),
              ...(llmPatch.feature_policies ?? {}),
            },
            providers: {
              ...(base.llm.providers ?? {}),
              ...(llmPatch.providers ?? {}),
            },
          };
    return {
      ok: true,
      settings: {
        ...base,
        ...patch,
        llm,
      },
    };
  });
}

function stubLlmSetDisabled(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.settings.set_disabled', (params) => {
    const disabled = Boolean((params as { disabled?: boolean }).disabled);
    return {
      ok: true,
      settings: settingsWire(store, { ...store.getState().settings.llm, disabled }),
    };
  });
}

function stubLlmProviderUpdate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.provider.update', (params) => {
    const { provider_id, patch } = params as {
      provider_id: string;
      patch: Record<string, unknown>;
    };
    const prev = store.getState().settings.llm;
    const existing = prev.providers?.[provider_id] ?? {};
    const next: LlmWire = {
      ...prev,
      providers: {
        ...(prev.providers ?? {}),
        [provider_id]: { ...existing, ...patch },
      },
    };
    return { ok: true, settings: settingsWire(store, next) };
  });
}

function stubLlmProviderCreate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.provider.create', (params) => {
    const provider = (params as { provider: Record<string, unknown> }).provider;
    const id = 'custom-local';
    const prev = store.getState().settings.llm;
    const next: LlmWire = {
      ...prev,
      providers: {
        ...(prev.providers ?? {}),
        [id]: provider,
      },
    };
    return { ok: true, provider_id: id, settings: settingsWire(store, next) };
  });
}

function stubLlmProviderDelete(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.provider.delete', (params) => {
    const provider_id = (params as { provider_id: string }).provider_id;
    const prev = store.getState().settings.llm;
    const providers = { ...(prev.providers ?? {}) };
    delete providers[provider_id];
    return {
      ok: true,
      settings: settingsWire(store, { ...prev, providers }),
    };
  });
}

function stubLlmPolicyActivate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.policy.activate', (params) => {
    const policy_id = (params as { policy_id: string }).policy_id;
    const prev = store.getState().settings.llm;
    return {
      ok: true,
      settings: settingsWire(store, { ...prev, active_policy: policy_id }),
    };
  });
}

function stubLlmPolicyCreate(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.policy.create', (params) => {
    const { name, policy } = params as {
      name: string;
      policy?: { groups?: readonly { readonly selectors: readonly unknown[] }[] };
    };
    const id = 'custom-policy';
    const prev = store.getState().settings.llm;
    const next: LlmWire = {
      ...prev,
      policies: {
        ...(prev.policies ?? {}),
        [id]: {
          builtin: false,
          name,
          groups: policy?.groups ?? [],
        },
      },
    };
    return { ok: true, policy_id: id, settings: settingsWire(store, next) };
  });
}

function stubLlmDiscoverModels(store: AppStoreApi, bus: FakeApplicationClient): void {
  bus.stubCommand('llm.provider.discover_models', (params) => {
    const provider_id = (params as { provider_id: string }).provider_id;
    const prev = store.getState().settings.llm;
    const existing = prev.providers?.[provider_id] ?? {};
    const models = ['model-a', 'model-b'];
    // Persist discovery onto the slice so the subsequent settings.load / store read sees it.
    // The real RPC saves server-side; FakeApplicationClient only returns the command result.
    store.setState({
      settings: {
        ...store.getState().settings,
        llm: {
          ...prev,
          providers: {
            ...(prev.providers ?? {}),
            [provider_id]: {
              ...existing,
              models: {
                ...(existing.models ?? {}),
                discovered: models,
                discovery_error: null,
              },
            },
          },
        },
      },
    });
    return {
      ok: true,
      models: models.map((id) => ({ id, label: id })),
      message: null,
    };
  });
  bus.stubQuery('settings.get', () => ({
    ok: true,
    settings: settingsWire(store),
  }));
}

afterEach(() => {
  cleanup();
  setTheme('everforest-dark');
  document.documentElement.style.removeProperty('--chrome-opacity');
  document.documentElement.style.removeProperty('--background-transparency');
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

describe('SettingsPanel appearance + depth (C1/C2)', () => {
  it('persists background_transparency and writes chrome opacity CSS vars', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('radio', { name: '50%' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { background_transparency: 50 } },
    });
    expect(document.documentElement.style.getPropertyValue('--background-transparency')).toBe('50');
    expect(document.documentElement.style.getPropertyValue('--chrome-opacity')).toBe('0.55');
  });

  it('persists default_chat_view_mode and document_display_mode', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('radio', { name: 'condensed' }));
    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { default_chat_view_mode: 'condensed' } },
    });

    fireEvent.click(screen.getByRole('radio', { name: 'markdown' }));
    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { document_display_mode: 'markdown' } },
    });
  });

  it('persists workspace_count', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('workspace count'), { target: { value: '4' } });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { workspace_count: 4 } },
    });
  });

  it('persists bar_widgets enabled toggles', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('switch', { name: 'Contextual hints' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: {
        settings: {
          bar_widgets: {
            hints: { enabled: false, placement: 'bottom', adaptive: true },
          },
        },
      },
    });
  });

  it('persists key_overrides when a rebindable action key is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'rebind spawn' }));
    fireEvent.keyDown(document, { key: 'q' });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { key_overrides: { 'global.spawn': 'q' } } },
    });
  });

  it('toggles LLM disabled via llm.settings.set_disabled', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmSetDisabled(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('switch', { name: 'enable LLM features' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.settings.set_disabled',
      params: { disabled: true },
    });
    // setDisabled applies the reply asynchronously (no optimistic overlay).
    await waitFor(() => {
      expect(store.getState().settings.llm.disabled).toBe(true);
    });
  });
});

describe('SettingsPanel LLM section', () => {
  it('toggles a builtin provider via llm.provider.update', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmProviderUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('switch', { name: 'groq' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.provider.update',
      params: { provider_id: 'groq', patch: { enabled: true } },
    });
    await waitFor(() => {
      expect(store.getState().settings.llm.providers?.['groq']?.enabled).toBe(true);
    });
  });

  it('creates a custom openai-compatible provider', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmProviderCreate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('name'), { target: { value: 'Home lab' } });
    fireEvent.change(screen.getByLabelText('endpoint'), {
      target: { value: 'http://127.0.0.1:8080/v1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.provider.create',
      params: {
        provider: {
          type: 'openai_compatible',
          enabled: false,
          name: 'Home lab',
          endpoint: 'http://127.0.0.1:8080/v1',
          auth: { source: 'environment' },
        },
      },
    });
    await waitFor(() => {
      expect(store.getState().settings.llm.providers?.['custom-local']).toMatchObject({
        name: 'Home lab',
        endpoint: 'http://127.0.0.1:8080/v1',
      });
    });
  });

  it('edits and removes a custom provider', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmProviderUpdate(store, bus);
    stubLlmProviderDelete(store, bus);
    store.setState({
      settings: {
        ...store.getState().settings,
        llm: {
          providers: {
            'my-box': {
              type: 'openai_compatible',
              name: 'My box',
              endpoint: 'http://localhost:9',
              enabled: true,
              auth: { source: 'none' },
            },
          },
        },
      },
    });
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'edit my-box' }));
    const form = screen.getByDisplayValue('My box').closest('.settings__provider-form');
    expect(form).not.toBeNull();
    fireEvent.change(within(form as HTMLElement).getByLabelText('name'), {
      target: { value: 'Renamed box' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save provider' }));

    expect(bus.commandCalls.at(-1)?.name).toBe('llm.provider.update');
    expect(bus.commandCalls.at(-1)?.params).toMatchObject({
      provider_id: 'my-box',
      patch: {
        name: 'Renamed box',
        endpoint: 'http://localhost:9',
        auth: { source: 'none', api_key: '***' },
      },
    });

    await waitFor(() => {
      expect(store.getState().settings.llm.providers?.['my-box']?.name).toBe('Renamed box');
    });

    fireEvent.click(screen.getByRole('button', { name: 'remove my-box' }));
    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.provider.delete',
      params: { provider_id: 'my-box', confirm: true },
    });
    await waitFor(() => {
      expect(store.getState().settings.llm.providers?.['my-box']).toBeUndefined();
    });
  });

  it('persists feature_policies via settings.update', () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubSettingsUpdate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('crow classification'), {
      target: { value: 'local-only' },
    });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'settings.update',
      params: { settings: { llm: { feature_policies: { crow_classification: 'local-only' } } } },
    });
  });

  it('activates a policy via llm.policy.activate', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmPolicyActivate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.change(screen.getByLabelText('active policy'), {
      target: { value: 'remote-free' },
    });

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.policy.activate',
      params: { policy_id: 'remote-free' },
    });
    await waitFor(() => {
      expect(store.getState().settings.llm.active_policy).toBe('remote-free');
    });
  });

  it('creates a custom policy via llm.policy.create', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmPolicyCreate(store, bus);
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'Create policy' }));
    fireEvent.change(screen.getByLabelText('policy name'), {
      target: { value: 'lab only' },
    });
    fireEvent.change(screen.getByLabelText('groups JSON'), {
      target: {
        value: JSON.stringify([{ selectors: [{ match: { locality: 'local' } }] }]),
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save new policy' }));

    expect(bus.commandCalls.at(-1)).toEqual({
      name: 'llm.policy.create',
      params: {
        name: 'lab only',
        policy: {
          groups: [{ selectors: [{ match: { locality: 'local' } }] }],
        },
      },
    });
    await waitFor(() => {
      expect(store.getState().settings.llm.policies?.['custom-policy']).toMatchObject({
        name: 'lab only',
        builtin: false,
      });
    });
  });

  it('discovers models for an edited provider', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    stubLlmDiscoverModels(store, bus);
    store.setState({
      settings: {
        ...store.getState().settings,
        llm: {
          providers: {
            groq: {
              enabled: true,
              auth: { source: 'environment' },
              models: { source: 'recommended', discovered: [] },
            },
          },
        },
      },
    });
    renderWithStore(<Harness />, { store, bus });

    fireEvent.click(screen.getByRole('button', { name: 'edit groq' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discover models' }));

    expect(bus.commandCalls.some((c) => c.name === 'llm.provider.discover_models')).toBe(true);
    expect(bus.commandCalls.at(-1)?.name).toBe('llm.provider.discover_models');
    expect(bus.commandCalls.at(-1)?.params).toEqual({ provider_id: 'groq' });

    await waitFor(() => {
      expect(store.getState().settings.llm.providers?.['groq']?.models?.discovered).toEqual([
        'model-a',
        'model-b',
      ]);
    });
    await waitFor(() => {
      expect(screen.getByText(/Available models: model-a, model-b/)).toBeTruthy();
    });
  });
});
