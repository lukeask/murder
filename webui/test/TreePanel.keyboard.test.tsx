/**
 * TreePanel keyboard transit nav — hjkl cursor, g-duration jump, paneUi persistence.
 */

import type { TransitLane } from '@murder/ui-core/store/transit/transitSlice.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { TreePanel } from '../src/components/panels/TreePanel.js';
import { panelFocusStore } from '../src/panelFocus.js';
import { createComposerStores } from '../src/composer/createComposerStores.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(() => {
  cleanup();
  panelFocusStore.getState().clear();
});

const NOW = Math.floor(Date.now() / 1000);

function commit(
  sha: string,
  subject: string,
  ageSec: number,
): TransitLane['commits'][number] {
  return {
    sha,
    short: sha.slice(0, 7),
    subject,
    body: '',
    tsEpoch: NOW - ageSec,
    parents: [],
  };
}

function lane(over: Partial<TransitLane>): TransitLane {
  return {
    branch: 'main',
    isMain: true,
    worktreePath: null,
    headSha: 'aaaaaaa0000',
    forkSha: null,
    commits: [commit('aaaaaaa0000', 'newest main', 60)],
    ...over,
  };
}

describe('TreePanel keyboard transit', () => {
  it('navigates commits with h/l and lanes with j/k while focused', () => {
    const { store, composer } = makeStoreWithComposer();
    seedSlice(store, 'transit', {
      lanes: [
        lane({
          commits: [
            commit('aaaaaaa0000', 'newest main', 60),
            commit('bbbbbbb0000', 'older main', 3600),
          ],
        }),
        lane({
          branch: 'feature',
          isMain: false,
          headSha: 'ccccccc0000',
          commits: [commit('ccccccc0000', 'feature tip', 120)],
        }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<TreePanel />, { store, composer });
    act(() => {
      panelFocusStore.getState().focus('tree');
    });

    // Defaults to lane 0 head.
    expect(document.querySelector('.transit-commit[data-selected="true"]')?.textContent).toContain(
      'newest main',
    );

    act(() => {
      fireEvent.keyDown(document, { key: 'h' });
    });
    expect(document.querySelector('.transit-commit[data-selected="true"]')?.textContent).toContain(
      'older main',
    );

    act(() => {
      fireEvent.keyDown(document, { key: 'l' });
    });
    expect(document.querySelector('.transit-commit[data-selected="true"]')?.textContent).toContain(
      'newest main',
    );

    act(() => {
      fireEvent.keyDown(document, { key: 'j' });
    });
    expect(document.querySelector('.transit-lane[data-cursor-lane="true"]')?.textContent).toContain(
      'feature',
    );
    expect(document.querySelector('.transit-commit[data-selected="true"]')?.textContent).toContain(
      'feature tip',
    );

    expect(composer.paneUi.getState().transitCursors['tree']?.sha).toBe('ccccccc0000');
  });

  it('resolves g + duration jump onto the selected lane', () => {
    const { store, composer } = makeStoreWithComposer();
    seedSlice(store, 'transit', {
      lanes: [
        lane({
          commits: [
            commit('aaaaaaa0000', 'newest', 60),
            commit('ddddddd0000', 'day ago', 86_400),
          ],
        }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<TreePanel />, { store, composer });
    act(() => {
      panelFocusStore.getState().focus('tree');
    });

    act(() => {
      fireEvent.keyDown(document, { key: 'g' });
    });
    expect(document.querySelector('.transit-jump')).toBeTruthy();
    act(() => {
      fireEvent.keyDown(document, { key: '1' });
      fireEvent.keyDown(document, { key: 'd' });
      fireEvent.keyDown(document, { key: 'Enter' });
    });

    expect(document.querySelector('.transit-jump')).toBeNull();
    expect(document.querySelector('.transit-commit[data-selected="true"]')?.textContent).toContain(
      'day ago',
    );
    expect(composer.paneUi.getState().gBuffers['tree'] ?? null).toBeNull();
  });

  it('still opens detail on click', () => {
    const { store } = makeStore();
    seedSlice(store, 'transit', {
      lanes: [
        lane({
          commits: [commit('aaaaaaa0000', 'split orchestrator', 3600)],
        }),
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<TreePanel />, { store });
    expect(screen.getByText('Git Tree')).toBeTruthy();
    fireEvent.click(document.querySelector('.transit-commit') as HTMLElement);
    expect(document.querySelector('.transit-detail')).toBeTruthy();
  });
});

function makeStoreWithComposer(): {
  store: ReturnType<typeof makeStore>['store'];
  composer: ReturnType<typeof createComposerStores>;
} {
  const { store } = makeStore();
  return { store, composer: createComposerStores() };
}
