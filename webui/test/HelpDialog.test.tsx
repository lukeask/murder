/** HelpDialog groups track live bindings (modifier + overrides) via buildHelpGroups. */

import { cleanup, fireEvent, screen, act } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { buildWebHelpGroups, HelpDialog } from '../src/components/modals/HelpDialog.js';
import { makeStore, renderWithStore } from './helpers.js';

afterEach(cleanup);

describe('HelpDialog', () => {
  it('renders global and command groups including ? and :help', () => {
    const groups = buildWebHelpGroups();
    expect(groups.some((g) => g.title === 'Global')).toBe(true);
    expect(groups.some((g) => g.title === 'Commands')).toBe(true);
    expect(groups.some((g) => g.title === 'Composer')).toBe(true);
    expect(groups.some((g) => g.title === 'Crows panel')).toBe(true);
    expect(groups.some((g) => g.title === 'History panel')).toBe(true);
    expect(groups.some((g) => g.title === 'Stage document')).toBe(true);
    expect(groups.some((g) => g.title === 'Stage transcript')).toBe(true);
    const keys = groups.flatMap((g) => g.entries.map((e) => e.key));
    expect(keys).toContain('?');
    expect(keys).toContain(':help');
    expect(keys.some((k) => k.includes('S-j') || k.includes('workspace'))).toBe(true);
    const helpEntry = groups
      .find((g) => g.title === 'Commands')
      ?.entries.find((e) => e.key === ':help');
    expect(helpEntry?.description).toBe('this dialog');

    // Large page so Commands stays on page 1 for the render smoke check.
    renderWithStore(<HelpDialog onClose={() => {}} groups={groups} rowsPerPage={200} />);
    expect(screen.getByText('Help')).toBeTruthy();
    expect(screen.getByText(':help')).toBeTruthy();
    expect(screen.getByText('this dialog')).toBeTruthy();
  });

  it('tracks key overrides in Global labels', () => {
    const groups = buildWebHelpGroups('alt', { 'global.spawn': 'q' });
    const spawn = groups
      .find((g) => g.title === 'Global')
      ?.entries.find((e) => e.description === 'spawn');
    expect(spawn?.key).toBe('A-q');
  });

  it('reads live settings overrides from the store', () => {
    const { store } = makeStore();
    store.setState((s) => ({
      ...s,
      settings: { ...s.settings, keyOverrides: { 'global.spawn': 'q' }, modifier: 'ctrl' },
    }));
    renderWithStore(<HelpDialog onClose={() => {}} />, { store });
    expect(screen.getByText('C-q')).toBeTruthy();
  });

  it('paginates with h/l when content exceeds rowsPerPage', () => {
    const groups = buildWebHelpGroups();
    renderWithStore(<HelpDialog onClose={() => {}} groups={groups} rowsPerPage={6} />);
    expect(screen.getByText(/page 1\//)).toBeTruthy();
    act(() => {
      fireEvent.keyDown(document, { key: 'l' });
    });
    expect(screen.getByText(/page 2\//)).toBeTruthy();
  });
});
