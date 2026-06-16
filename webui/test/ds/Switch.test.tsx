/**
 * Switch smoke test: renders a role=switch, controlled checked + disabled + className map to
 * `.mds-switch*` classes, uncontrolled toggles, and ref + onChange forward.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Switch } from '../../src/components/ds/Switch.js';

afterEach(cleanup);

describe('ds/Switch', () => {
  it('renders off by default with no --on modifier', () => {
    const { container } = render(<Switch label="Notify" />);
    const root = container.querySelector('.mds-switch');
    expect(root).not.toBeNull();
    expect(root?.className).not.toContain('mds-switch--on');
    expect(screen.getByRole('switch')).toBeTruthy();
    expect(screen.getByText('Notify')).toBeTruthy();
  });

  it('applies --on / --disabled + className when controlled-checked', () => {
    const { container } = render(<Switch checked disabled className="extra" />);
    const root = container.querySelector('.mds-switch');
    expect(root?.className).toContain('mds-switch--on');
    expect(root?.className).toContain('mds-switch--disabled');
    expect(root?.className).toContain('extra');
  });

  it('toggles uncontrolled and forwards ref + onChange', () => {
    const ref = createRef<HTMLInputElement>();
    const onChange = vi.fn();
    const { container } = render(<Switch ref={ref} onChange={onChange} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledOnce();
    expect(container.querySelector('.mds-switch')?.className).toContain('mds-switch--on');
  });
});
