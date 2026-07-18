/**
 * Themes actions tests — load registers palettes; save/import/remove round-trip via RPC.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { createAppStore } from '../../src/store/store.js';
import { selectLiveToasts, toastStore } from '../../src/store/toast/toastStore.js';
import { everforestDarkHard, hasTheme, listThemeIds } from '../../src/theme/palettes.js';

function sampleThemeRecord(id: string) {
  return {
    id,
    name: id,
    variant: 'dark' as const,
    builtin: false,
    palette: { ...everforestDarkHard },
  };
}

function setup() {
  const fake = new FakeBusClient();
  fake.stubQuery('themes.get', { ok: true, themes: [] });
  fake.stubCommand('themes.set', (params) => ({ ok: true, themes: params.themes }));
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('themes actions', () => {
  beforeEach(() => {
    toastStore.getState().clear();
  });

  it('load() registers palettes from themes.get', async () => {
    const { fake, store, dispose } = setup();
    fake.stubQuery('themes.get', {
      ok: true,
      themes: [sampleThemeRecord('tokyo-night')],
    });

    await store.getState().actions.themes.load();

    expect(hasTheme('tokyo-night')).toBe(true);
    expect(store.getState().themes.items).toHaveLength(1);
    dispose();
  });

  it('importTheme() appends via theme.import and registers the new id', async () => {
    const { fake, store, dispose } = setup();
    const custom = sampleThemeRecord('my-theme');
    fake.stubCommand('theme.import', () => ({
      ok: true,
      id: custom.id,
      themes: [custom],
    }));

    const id = await store.getState().actions.themes.importTheme(JSON.stringify(custom));
    expect(id).toBe('my-theme');
    expect(hasTheme('my-theme')).toBe(true);
    expect(listThemeIds()).toContain('my-theme');
    dispose();
  });

  it('remove() drops a custom theme and persists the reduced list', async () => {
    const { fake, store, dispose } = setup();
    const custom = sampleThemeRecord('drop-me');
    fake.stubQuery('themes.get', { ok: true, themes: [custom] });
    await store.getState().actions.themes.load();

    await store.getState().actions.themes.remove('drop-me');

    expect(store.getState().themes.items.some((t) => t.id === 'drop-me')).toBe(false);
    expect(fake.commandCalls.some((c) => c.name === 'themes.set')).toBe(true);
    dispose();
  });

  it('save rejection surfaces via toast without rolling back the optimistic list', async () => {
    const { fake, store, dispose } = setup();
    const custom = sampleThemeRecord('optimistic');
    store.setState({ themes: { items: [custom], status: 'ready', error: null } });
    fake.stubCommand('themes.set', () => {
      throw new Error('disk full');
    });

    await store.getState().actions.themes.save([custom]);

    expect(store.getState().themes.items).toHaveLength(1);
    expect(
      selectLiveToasts(toastStore.getState().toasts, Date.now()).some(
        (t) => t.severity === 'error',
      ),
    ).toBe(true);
    dispose();
  });
});
