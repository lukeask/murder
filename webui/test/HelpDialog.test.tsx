/** HelpDialog static groups cover desktop keybinds + chat commands. */

import { cleanup, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { buildWebHelpGroups, HelpDialog } from '../src/components/modals/HelpDialog.js';
import { renderWithStore } from './helpers.js';

afterEach(cleanup);

describe('HelpDialog', () => {
  it('renders global and command groups including ? and :help', () => {
    const groups = buildWebHelpGroups();
    expect(groups.some((g) => g.title === 'Global')).toBe(true);
    expect(groups.some((g) => g.title === 'Commands')).toBe(true);
    const keys = groups.flatMap((g) => g.entries.map((e) => e.key));
    expect(keys).toContain('?');
    expect(keys).toContain(':help');

    renderWithStore(<HelpDialog onClose={() => {}} />);
    expect(screen.getByText('Help')).toBeTruthy();
    expect(screen.getByText(':help')).toBeTruthy();
    expect(screen.getByText('this dialog')).toBeTruthy();
  });
});
