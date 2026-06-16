/**
 * Tabs smoke test: renders string + object tabs, marks the active tab, fires onChange(id), and the
 * variant/full props map to the right `.mds-tabs*` classes. Pins the navigation exemplar contract.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Tabs } from '../../src/components/ds/Tabs.js';

afterEach(cleanup);

describe('ds/Tabs', () => {
  it('renders tabs, marks the active one, and applies the variant class', () => {
    const { container } = render(
      <Tabs tabs={['Plans', 'Tickets']} value="Tickets" variant="pill" full />,
    );
    const list = container.querySelector('.mds-tabs');
    expect(list?.className).toContain('mds-tabs--pill');
    expect(list?.className).toContain('mds-tabs--full');
    const active = screen.getByRole('tab', { name: 'Tickets' });
    expect(active.className).toContain('mds-tab--active');
    expect(active.getAttribute('aria-selected')).toBe('true');
  });

  it('fires onChange with the clicked tab id', () => {
    const onChange = vi.fn();
    render(<Tabs tabs={['a', 'b']} value="a" onChange={onChange} />);
    fireEvent.click(screen.getByRole('tab', { name: 'b' }));
    expect(onChange).toHaveBeenCalledWith('b');
  });

  it('renders object tabs with count and icon (stack modifier)', () => {
    render(
      <Tabs
        tabs={[{ id: 'crows', label: 'Crows', count: 5, icon: <svg /> }]}
        value="crows"
      />,
    );
    const tab = screen.getByRole('tab', { name: /Crows/ });
    expect(tab.className).toContain('mds-tab--stack');
    expect(screen.getByText('5').className).toContain('mds-tab__count');
  });
});
