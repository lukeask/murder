/**
 * Settings actions tests — the `settings.{get,update}` prefs pipeline (rule 3: actions are the only
 * bus path). Mirrors the favorites actions test.
 *
 * Drives the settings slice through a `FakeBusClient`:
 *  - `load()` fires `settings.get` and fills the slice from the reply (wire `key_overrides` →
 *     slice `keyOverrides`).
 *  - `update(partial)` overlays the patch locally (optimistic) AND fires `settings.update` with the
 *     same partial — the persist assertion.
 *  - a save rejection lands in `error` + a toast without rolling back the optimistic local change.
 *  - a load rejection sets status=error and leaves settings at their defaults.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import type { SettingsWire } from '../../src/store/settings/settingsActions.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';

/** All live error toasts on the singleton at the current instant. */
function errorToasts() {
  const live = selectLiveToasts(toastStore.getState().toasts, Date.now());
  return live.filter((t) => t.severity === 'error');
}

/** A full default settings wire record (every extended field present), so a stubbed reply mirrors the
 * real `_settings_payload`. Override fields per test via `{ ...wire(), ... }`. */
function wire(over: Record<string, unknown> = {}): SettingsWire {
  return {
    theme: 'everforest-dark',
    modifier: 'alt',
    key_overrides: {},
    pane_gap: 0,
    workspace_count: 1,
    vim_mode: false,
    bar_widgets: {},
    default_chat_view_mode: 'verbose',
    document_display_mode: 'plain',
    startup_rogue: null,
    collaborator_harness: null,
    planner_harness: null,
    crow_harnesses: null,
    effective_collaborator_harness: 'claude_code',
    effective_planner_harness: 'claude_code',
    effective_crow_harnesses: ['claude_code'],
    llm: {},
    llm_env: { groq: false, cerebras: false, openrouter: false },
    ...over,
  } as SettingsWire;
}

function setup() {
  const fake = new FakeBusClient();
  // Default stubs so an unrelated load/update resolves; tests override as needed. `settings.update`
  // echoes the patch onto the default record, mirroring the server's "reply = full merged payload".
  fake.stubQuery('settings.get', { ok: true, settings: wire() });
  fake.stubCommand('settings.update', (params) => {
    const partial = (params.settings ?? {}) as Record<string, unknown>;
    return { ok: true, settings: wire(partial) };
  });
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('settings actions', () => {
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() fires settings.get and fills the slice (key_overrides → keyOverrides)', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', {
      ok: true,
      settings: wire({
        theme: 'everforest-light',
        modifier: 'ctrl',
        key_overrides: { 'global.spawn': 'x' },
        pane_gap: 3,
      }),
    });

    await store.getState().actions.settings.load();

    expect(fake.queryCalls.some((c) => c.name === 'settings.get')).toBe(true);
    const s = store.getState().settings;
    expect(s.status).toBe('ready');
    expect(s.theme).toBe('everforest-light');
    expect(s.modifier).toBe('ctrl');
    expect(s.keyOverrides).toEqual({ 'global.spawn': 'x' });
    expect(s.paneGap).toBe(3);
    dispose();
  });

  it('update(pane_gap) overlays locally AND persists via settings.update', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ pane_gap: 2 });

    expect(store.getState().settings.paneGap).toBe(2);
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { pane_gap: 2 } });
    dispose();
  });

  it('update(workspace_count) overlays locally AND persists via settings.update', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ workspace_count: 3 });

    expect(store.getState().settings.workspaceCount).toBe(3);
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { workspace_count: 3 } });
    dispose();
  });

  it('vimMode defaults to false before any load', () => {
    const { store, dispose } = setup();
    expect(store.getState().settings.vimMode).toBe(false);
    dispose();
  });

  it('load() fills vimMode from the wire (vim_mode → vimMode)', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', { ok: true, settings: wire({ vim_mode: true }) });
    await store.getState().actions.settings.load();
    expect(store.getState().settings.vimMode).toBe(true);
    dispose();
  });

  it('update(vim_mode) overlays locally AND persists via settings.update', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ vim_mode: true });

    expect(store.getState().settings.vimMode).toBe(true);
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { vim_mode: true } });
    dispose();
  });

  it('load() fills defaultChatViewMode from the wire (TUIchat-3)', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', {
      ok: true,
      settings: wire({ default_chat_view_mode: 'condensed' }),
    });
    await store.getState().actions.settings.load();
    expect(store.getState().settings.defaultChatViewMode).toBe('condensed');
    dispose();
  });

  it('update(default_chat_view_mode) overlays locally AND persists (TUIchat-3)', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ default_chat_view_mode: 'condensed' });

    expect(store.getState().settings.defaultChatViewMode).toBe('condensed');
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { default_chat_view_mode: 'condensed' } });
    dispose();
  });

  it('loads and optimistically persists document display mode', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', {
      ok: true,
      settings: wire({ document_display_mode: 'markdown' }),
    });
    await store.getState().actions.settings.load();
    expect(store.getState().settings.documentDisplayMode).toBe('markdown');

    await store.getState().actions.settings.update({ document_display_mode: 'plain' });
    expect(store.getState().settings.documentDisplayMode).toBe('plain');
    const updates = fake.commandCalls.filter((call) => call.name === 'settings.update');
    expect(updates[0]?.params).toEqual({ settings: { document_display_mode: 'plain' } });
    dispose();
  });

  it('update(partial) overlays locally AND persists via settings.update', async () => {
    const { fake, store, dispose } = setup();

    await store.getState().actions.settings.update({ modifier: 'ctrl' });

    // Optimistic local overlay.
    expect(store.getState().settings.modifier).toBe('ctrl');
    // Persisted with the same partial.
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates.length).toBe(1);
    expect(updates[0]?.params).toEqual({ settings: { modifier: 'ctrl' } });
    dispose();
  });

  it('update with key_overrides mirrors onto the slice keyOverrides', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.settings.update({ key_overrides: { 'global.tmux': 'g' } });
    expect(store.getState().settings.keyOverrides).toEqual({ 'global.tmux': 'g' });
    dispose();
  });

  it('a save rejection sets error, keeps the optimistic local change, AND surfaces a toast', async () => {
    const { fake, store, dispose } = setup();
    fake.stubCommand('settings.update', () => {
      throw new Error('rpc error [internal]: bus down');
    });

    await store.getState().actions.settings.update({ modifier: 'both' });
    const s = store.getState().settings;
    // Local change still reflects the user's intent (no rollback).
    expect(s.modifier).toBe('both');
    expect(s.error).toBe('rpc error [internal]: bus down');
    const errs = errorToasts();
    expect(errs).toHaveLength(1);
    expect(errs[0]?.text).toBe('rpc error [internal]: bus down');
    dispose();
  });

  it('a successful update pushes NO error toast', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.settings.update({ theme: 'everforest-light' });
    expect(errorToasts()).toHaveLength(0);
    dispose();
  });

  it('load() fills the extended harness + llm fields (snake_case → camelCase)', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', {
      ok: true,
      settings: wire({
        collaborator_harness: 'codex',
        planner_harness: 'cursor',
        crow_harnesses: ['cursor', 'pi'],
        effective_collaborator_harness: 'codex',
        effective_planner_harness: 'cursor',
        effective_crow_harnesses: ['cursor', 'pi'],
        llm: {
          providers: { groq: { api_key: '***', base_url: null } },
          tiers: { fast: { provider: 'groq', model: 'm', auto_free: true } },
          roles: { notetaker: 'fast' },
        },
        llm_env: { groq: true, cerebras: false, openrouter: false },
      }),
    });

    await store.getState().actions.settings.load();
    const s = store.getState().settings;
    expect(s.collaboratorHarness).toBe('codex');
    expect(s.plannerHarness).toBe('cursor');
    expect(s.crowHarnesses).toEqual(['cursor', 'pi']);
    expect(s.effectiveCollaboratorHarness).toBe('codex');
    expect(s.effectivePlannerHarness).toBe('cursor');
    expect(s.effectiveCrowHarnesses).toEqual(['cursor', 'pi']);
    expect(s.llm.providers?.['groq']?.api_key).toBe('***');
    expect(s.llm.roles).toEqual({ notetaker: 'fast' });
    expect(s.llmEnv.groq).toBe(true);
    dispose();
  });

  it('update(collaborator_harness) overlays optimistically AND persists', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.settings.update({ collaborator_harness: 'codex' });
    expect(store.getState().settings.collaboratorHarness).toBe('codex');
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates[0]?.params).toEqual({ settings: { collaborator_harness: 'codex' } });
    dispose();
  });

  it('update(collaborator_harness: null) clears the override locally', async () => {
    const { store, dispose } = setup();
    // First set it, then clear it.
    await store.getState().actions.settings.update({ collaborator_harness: 'codex' });
    await store.getState().actions.settings.update({ collaborator_harness: null });
    expect(store.getState().settings.collaboratorHarness).toBeNull();
    dispose();
  });

  it('update(planner_harness) overlays optimistically AND persists', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.settings.update({ planner_harness: 'codex' });
    expect(store.getState().settings.plannerHarness).toBe('codex');
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates[0]?.params).toEqual({ settings: { planner_harness: 'codex' } });
    dispose();
  });

  it('update(planner_harness: null) clears the override locally', async () => {
    const { store, dispose } = setup();
    await store.getState().actions.settings.update({ planner_harness: 'codex' });
    await store.getState().actions.settings.update({ planner_harness: null });
    expect(store.getState().settings.plannerHarness).toBeNull();
    dispose();
  });

  it('update(crow_harnesses) overlays the list optimistically AND persists', async () => {
    const { fake, store, dispose } = setup();
    await store.getState().actions.settings.update({ crow_harnesses: ['cursor', 'pi'] });
    expect(store.getState().settings.crowHarnesses).toEqual(['cursor', 'pi']);
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates[0]?.params).toEqual({ settings: { crow_harnesses: ['cursor', 'pi'] } });
    dispose();
  });

  it('update(llm) persists the patch and refreshes llm from the reply', async () => {
    const { fake, store, dispose } = setup();
    // The echo stub reflects the patch back as the merged llm payload.
    await store.getState().actions.settings.update({ llm: { roles: { notetaker: 'smart' } } });
    const updates = fake.commandCalls.filter((c) => c.name === 'settings.update');
    expect(updates[0]?.params).toEqual({ settings: { llm: { roles: { notetaker: 'smart' } } } });
    expect(store.getState().settings.llm.roles).toEqual({ notetaker: 'smart' });
    dispose();
  });

  it('a load rejection sets status=error and leaves settings at defaults', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('settings.get', () => {
      throw new Error('no settings');
    });

    await store.getState().actions.settings.load();
    const s = store.getState().settings;
    expect(s.status).toBe('error');
    expect(s.error).toBe('no settings');
    expect(s.modifier).toBe('alt');
    expect(s.theme).toBe('everforest-dark');
    expect(s.keyOverrides).toEqual({});
    dispose();
  });
});
