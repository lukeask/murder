/**
 * Boot / ConnectedSession: repo-switch timing, dispose, and workspace hydration.
 */

import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { createAppStore } from '@murder/ui-core/store/store.js';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createComposerStores,
  toWorkspaceStores,
  webFreshWorkspaceSnapshot,
} from '../src/composer/createComposerStores.js';
import {
  parkWebRepositoryWorkspace,
  resumeWebRepositoryWorkspace,
} from '../src/composer/workspaceActions.js';
import { panelFocusStore } from '../src/panelFocus.js';

/**
 * Cross-mock recorder. `vi.hoisted` because mock factories cannot close over
 * per-test state; every App render snapshots the observable workspace state so
 * assertions can target the *first* paint rather than whatever rendered last.
 */
const probes = vi.hoisted(() => ({
  composer: null as import('../src/composer/createComposerStores.js').ComposerStores | null,
  renders: [] as Array<{ hint: string; draft: string; focus: string }>,
  disposeCalls: 0,
  closeCalls: 0,
}));

vi.mock('../src/App.js', () => ({
  App: ({ repositoryHint }: { readonly repositoryHint: string | null }) => {
    probes.renders.push({
      hint: repositoryHint ?? '',
      draft: probes.composer?.chatInput.getState().text ?? '',
      focus: panelFocusStore.getState().focusedId ?? 'chat',
    });
    return <div data-testid="app" />;
  },
}));

vi.mock('../src/application/createBrowserApplicationClient.js', () => ({
  createBrowserApplicationClient: () => {
    const bus = new FakeApplicationClient();
    Object.assign(bus, {
      connect: vi.fn(async () => undefined),
      close: vi.fn(() => {
        probes.closeCalls += 1;
      }),
    });
    return bus;
  },
  repositoryIdFromLocation: () => null,
}));

vi.mock('@murder/ui-core/store/store.js', async () => {
  const actual = await vi.importActual<typeof import('@murder/ui-core/store/store.js')>(
    '@murder/ui-core/store/store.js',
  );
  return {
    ...actual,
    createAppStore: (bus: Parameters<typeof actual.createAppStore>[0]) => {
      const created = actual.createAppStore(bus);
      return {
        store: created.store,
        dispose: () => {
          probes.disposeCalls += 1;
          created.dispose();
        },
      };
    },
  };
});

afterEach(() => {
  cleanup();
  panelFocusStore.getState().clear();
  probes.composer = null;
  probes.renders.length = 0;
  probes.disposeCalls = 0;
  probes.closeCalls = 0;
  vi.unstubAllGlobals();
});

beforeEach(() => {
  vi.stubGlobal('history', {
    replaceState: vi.fn(),
  });
  vi.stubGlobal('location', {
    href: 'http://localhost/',
    pathname: '/',
    search: '',
    hash: '',
    protocol: 'http:',
    host: 'localhost',
  });
});

describe('ConnectedSession repo-switch timing', () => {
  it('hydrates the incoming repo before first App paint and disposes on unmount', async () => {
    const { ConnectedSession } = await import('../src/Boot.js');
    const composer = createComposerStores();
    probes.composer = composer;
    const bootstrapBus = new FakeApplicationClient();
    const { store: bootstrapApp, dispose: disposeBootstrap } = createAppStore(bootstrapBus);
    const stores = toWorkspaceStores(composer, bootstrapApp);

    resumeWebRepositoryWorkspace(stores, 'repo-a', {
      freshSnapshot: webFreshWorkspaceSnapshot(),
    });
    panelFocusStore.getState().focus('history');
    composer.chatInput.getState().insert('draft-from-a');
    parkWebRepositoryWorkspace(stores, 'repo-a');
    disposeBootstrap();

    // Outgoing live state must not flash when opening a different repository.
    composer.chatInput.getState().insert('stale-live-draft');
    panelFocusStore.getState().focus('usage');

    const onSwitchRepo = vi.fn();
    const first = render(
      <ConnectedSession
        repositoryId="repo-b"
        rootPath="/tmp/beta"
        onSwitchRepo={onSwitchRepo}
        composer={composer}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('app')).toBeTruthy();
    });
    // First App paint must already show the incoming repo's hydrated state.
    expect(probes.renders[0]).toEqual({ hint: 'beta', draft: '', focus: 'chat' });
    expect(composer.workspace.getState().repositoryId).toBe('repo-b');

    first.unmount();

    // Remount onto parked repo-a: draft + panel focus restore without stale flash.
    const second = render(
      <ConnectedSession
        repositoryId="repo-a"
        rootPath="/tmp/alpha"
        onSwitchRepo={onSwitchRepo}
        composer={composer}
      />,
    );
    await waitFor(() => {
      expect(probes.renders.some((r) => r.hint === 'alpha')).toBe(true);
    });
    const alphaPaint = probes.renders.find((r) => r.hint === 'alpha');
    expect(alphaPaint).toEqual({ hint: 'alpha', draft: 'draft-from-a', focus: 'history' });

    // Delta assertions: the bootstrap store's dispose above must not count.
    const disposeBefore = probes.disposeCalls;
    const closeBefore = probes.closeCalls;
    second.unmount();
    expect(probes.disposeCalls).toBeGreaterThan(disposeBefore);
    expect(probes.closeCalls).toBeGreaterThan(closeBefore);
  });
});
