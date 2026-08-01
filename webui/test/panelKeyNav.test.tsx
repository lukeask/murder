/**
 * Panel focus + list keynav thin adapters (Wave 6).
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, act } from '@testing-library/react';
import { createPanelStore } from '@murder/ui-core/input/panelStore.js';
import { PANEL_IDS } from '@murder/ui-core/input/panels.js';
import { panelFocusStore, hopPanelFocus } from '../src/panelFocus.js';
import { togglePanelVisibility } from '../src/panelVisibility.js';
import { resolveProjectName } from '../src/projectName.js';
import { RosterPanel } from '../src/components/panels/RosterPanel.js';
import { HistoryPanel } from '../src/components/panels/HistoryPanel.js';
import { UsagePanel } from '../src/components/panels/UsagePanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';
import type { RosterRow } from '@murder/ui-core/store/roster/rosterSlice.js';

afterEach(() => {
  cleanup();
  panelFocusStore.getState().clear();
});

describe('panelFocus + visibility', () => {
  it('digit show focuses the panel; hide clears when it held focus', () => {
    const panels = createPanelStore(PANEL_IDS);
    panels.getState().hide('crows');
    expect(panelFocusStore.getState().focusedId).toBeNull();
    togglePanelVisibility(panels, 'crows');
    expect(panels.getState().visible.has('crows')).toBe(true);
    expect(panelFocusStore.getState().focusedId).toBe('crows');
    togglePanelVisibility(panels, 'crows');
    expect(panels.getState().visible.has('crows')).toBe(false);
    expect(panelFocusStore.getState().focusedId).toBeNull();
  });

  it('hopPanelFocus moves to nearest neighbour by DOM geometry', () => {
    const previous = document.body.innerHTML;
    document.body.innerHTML = `
      <div data-panel-id="history"></div>
      <div data-focus-id="stage"></div>
      <div data-panel-id="crows"></div>
    `;
    try {
      const stub = (el: Element, rect: DOMRect): void => {
        Object.defineProperty(el, 'getBoundingClientRect', {
          configurable: true,
          value: () => rect,
        });
      };
      stub(document.querySelector('[data-panel-id="history"]')!, {
        x: 0, y: 0, left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100,
        toJSON: () => ({}),
      } as DOMRect);
      stub(document.querySelector('[data-focus-id="stage"]')!, {
        x: 120, y: 0, left: 120, top: 0, width: 200, height: 200, right: 320, bottom: 200,
        toJSON: () => ({}),
      } as DOMRect);
      stub(document.querySelector('[data-panel-id="crows"]')!, {
        x: 340, y: 0, left: 340, top: 0, width: 100, height: 100, right: 440, bottom: 100,
        toJSON: () => ({}),
      } as DOMRect);

      panelFocusStore.getState().focus('history');
      expect(hopPanelFocus('right')).toBe('stage');
      expect(panelFocusStore.getState().focusedId).toBeNull();
      expect(hopPanelFocus('right')).toBe('crows');
      expect(panelFocusStore.getState().focusedId).toBe('crows');
      expect(hopPanelFocus('left')).toBe('stage');
    } finally {
      document.body.innerHTML = previous;
    }
  });
});

describe('resolveProjectName', () => {
  it('reads VITE_MURDER_PROJECT then MURDER_PROJECT', () => {
    expect(resolveProjectName({ VITE_MURDER_PROJECT: ' alpha ' })).toBe('alpha');
    expect(resolveProjectName({ MURDER_PROJECT: 'beta' })).toBe('beta');
    expect(resolveProjectName({})).toBeNull();
    expect(resolveProjectName({ VITE_MURDER_PROJECT: '  ' })).toBeNull();
  });

  it('prefers store/settings project over env', () => {
    expect(
      resolveProjectName({
        fromStore: ' from-roles ',
        env: { VITE_MURDER_PROJECT: 'from-env' },
      }),
    ).toBe('from-roles');
    expect(
      resolveProjectName({
        fromStore: null,
        env: { VITE_MURDER_PROJECT: 'from-env' },
      }),
    ).toBe('from-env');
    expect(resolveProjectName({ fromStore: '  ', env: { MURDER_PROJECT: 'env' } })).toBe('env');
  });
});

describe('RosterPanel keyboard', () => {
  it('j/k move cursor and f stars when focused', async () => {
    const { store } = makeStore();
    const toggle = vi.spyOn(store.getState().actions.favorites, 'toggle').mockResolvedValue();
    seedSlice(store, 'roster', {
      rows: [
        {
          agentId: 'a1',
          role: 'collaborator',
          ticketId: null,
          ticketTitle: null,
          harness: 'claude',
          model: 'opus',
          status: 'running',
          session: 's1',
        } satisfies RosterRow,
        {
          agentId: 'a2',
          role: 'crow',
          ticketId: null,
          ticketTitle: null,
          harness: 'codex',
          model: 'o3',
          status: 'idle',
          session: null,
        } satisfies RosterRow,
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store });
    act(() => {
      panelFocusStore.getState().focus('crows');
    });
    expect(document.querySelector('[data-panel-id="crows"]')?.getAttribute('data-focused')).toBe(
      'true',
    );
    act(() => {
      fireEvent.keyDown(document, { key: 'j' });
    });
    act(() => {
      fireEvent.keyDown(document, { key: 'f' });
    });
    expect(toggle).toHaveBeenCalledWith('a2');
  });

  it('collapses a section when its header is clicked', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', {
      rows: [
        {
          agentId: 'a1',
          role: 'collaborator',
          ticketId: null,
          ticketTitle: null,
          harness: 'claude',
          model: 'opus',
          status: 'running',
          session: 's1',
        } satisfies RosterRow,
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store });
    expect(document.querySelectorAll('.mds-row').length).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: /Collaborator/i }));
    expect(document.querySelectorAll('.mds-row').length).toBe(0);
  });

  it('m toggles compact meta density when focused', () => {
    const { store } = makeStore();
    seedSlice(store, 'roster', {
      rows: [
        {
          agentId: 'a1',
          role: 'collaborator',
          ticketId: null,
          ticketTitle: null,
          harness: 'claude',
          model: 'opus',
          status: 'running',
          session: 's1',
        } satisfies RosterRow,
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<RosterPanel />, { store });
    expect(document.querySelector('.roster-meta')).toBeTruthy();
    expect(document.querySelector('.roster-panel--compact')).toBeNull();
    act(() => {
      panelFocusStore.getState().focus('crows');
    });
    act(() => {
      fireEvent.keyDown(document, { key: 'm' });
    });
    expect(document.querySelector('.roster-panel--compact')).toBeTruthy();
    expect(document.querySelector('.roster-meta')).toBeNull();
    act(() => {
      fireEvent.keyDown(document, { key: 'm' });
    });
    expect(document.querySelector('.roster-panel--compact')).toBeNull();
    expect(document.querySelector('.roster-meta')).toBeTruthy();
  });
});

describe('HistoryPanel keyboard', () => {
  it('x dismisses the cursor row when focused', () => {
    const { store } = makeStore();
    const dismiss = vi.spyOn(store.getState().actions.history, 'dismiss').mockResolvedValue();
    seedSlice(store, 'history', {
      rows: [
        {
          itemId: 'h1',
          conversationId: 'c1',
          text: 'hello intent',
          target: 'crow-a',
          status: 'open',
          ts: '2026-06-15T01:00:00Z',
          harness: 'claude',
          conversationStatus: 'open',
          resumable: true,
        },
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<HistoryPanel />, { store });
    act(() => {
      panelFocusStore.getState().focus('history');
    });
    act(() => {
      fireEvent.keyDown(document, { key: 'x' });
    });
    expect(dismiss).toHaveBeenCalledWith('h1');
  });
});

describe('UsagePanel keyboard', () => {
  it('r samples when focused', () => {
    const { store } = makeStore();
    const sample = vi.spyOn(store.getState().actions.usage, 'sample').mockResolvedValue();
    seedSlice(store, 'usage', {
      rows: [
        {
          harness: 'claude',
          windowKey: '5h',
          pct: 20,
          tUntilResetMinutes: 60,
          tPeriodMinutes: 300,
          steering: 'auto',
        },
      ],
      status: 'ready',
      error: null,
    });
    renderWithStore(<UsagePanel />, { store });
    act(() => {
      panelFocusStore.getState().focus('usage');
    });
    act(() => {
      fireEvent.keyDown(document, { key: 'r' });
    });
    expect(sample).toHaveBeenCalled();
  });
});
