/** ListRow smoke test: renders, selected/star variants apply classes, `as` polymorphism, onPinToggle. */

import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ListRow } from '../../src/components/ds/ListRow.js';

afterEach(cleanup);

describe('ds/ListRow', () => {
  it('renders title, meta and trailing; selected applies the modifier', () => {
    const { container } = render(
      <ListRow title="plan-1" meta="2m ago" trailing="3" selected />,
    );
    expect(screen.getByText('plan-1').className).toContain('mds-row__title');
    expect(screen.getByText('2m ago').className).toContain('mds-row__meta');
    expect(screen.getByText('3').className).toContain('mds-row__trail');
    expect(container.querySelector('.mds-row')?.className).toContain('mds-row--selected');
  });

  it('renders the pin as a button when onPinToggle is set and fires (stopping propagation)', () => {
    const onPinToggle = vi.fn();
    const onRowClick = vi.fn();
    render(
      <ListRow title="t" starred onPinToggle={onPinToggle} onClick={onRowClick} />,
    );
    const btn = screen.getByRole('button', { name: 'unpin' });
    expect(btn.className).toContain('mds-row__star--on');
    fireEvent.click(btn);
    expect(onPinToggle).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('omits the star slot when starred is undefined; renders a non-button slot otherwise', () => {
    const { container, rerender } = render(<ListRow title="t" />);
    expect(container.querySelector('.mds-row__star')).toBeNull();
    rerender(<ListRow title="t" starred={false} />);
    const slot = container.querySelector('.mds-row__star');
    expect(slot).not.toBeNull();
    expect(slot?.tagName).toBe('SPAN');
    expect(slot?.className).not.toContain('mds-row__star--on');
  });

  it('renders clickable rows as div[role=button] so nested pin buttons stay valid', () => {
    const onRowClick = vi.fn();
    const { container } = render(
      <ListRow title="t" starred onPinToggle={vi.fn()} onClick={onRowClick} />,
    );
    const root = container.querySelector('.mds-row');
    expect(root?.tagName).toBe('DIV');
    expect(root?.getAttribute('role')).toBe('button');
    expect(screen.getByRole('button', { name: 'unpin' }).tagName).toBe('BUTTON');
  });

  it('honors the polymorphic `as` prop when the row is not interactive', () => {
    const { container } = render(<ListRow as="a" title="t" />);
    const root = container.querySelector('.mds-row');
    expect(root?.tagName).toBe('A');
  });
});
