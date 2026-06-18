/**
 * NavBar smoke test: renders string + object items, marks the active item, fires onSelect(id), and
 * surfaces the brand + trailing slot.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NavBar } from '../../src/components/ds/NavBar.js';

afterEach(cleanup);

describe('ds/NavBar', () => {
  it('renders items, marks the active one, and shows brand + trailing', () => {
    const { container } = render(
      <NavBar
        brand="murder"
        items={['plans', { id: 'tickets', label: 'tickets' }]}
        active="tickets"
        trailing={<span>trail</span>}
      />,
    );
    expect(container.querySelector('.mds-nav__brand')?.textContent).toBe('murder');
    const active = screen.getByRole('button', { name: 'tickets' });
    expect(active.className).toContain('mds-nav__item--active');
    expect(screen.getByRole('button', { name: 'plans' }).className).not.toContain(
      'mds-nav__item--active',
    );
    expect(container.querySelector('.mds-nav__trail')?.textContent).toBe('trail');
  });

  it('fires onSelect with the clicked item id', () => {
    const onSelect = vi.fn();
    render(<NavBar items={['a', 'b']} active="a" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: 'b' }));
    expect(onSelect).toHaveBeenCalledWith('b');
  });
});
