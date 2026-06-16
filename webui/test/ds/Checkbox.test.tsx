/**
 * Checkbox smoke test: renders, controlled checked + disabled + className map to `.mds-check*`
 * classes, the tick shows when on, uncontrolled toggles, and ref + onChange forward.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Checkbox } from '../../src/components/ds/Checkbox.js';

afterEach(cleanup);

describe('ds/Checkbox', () => {
  it('renders off by default with no --on modifier', () => {
    const { container } = render(<Checkbox label="Auto" />);
    const root = container.querySelector('.mds-check');
    expect(root).not.toBeNull();
    expect(root?.className).not.toContain('mds-check--on');
    expect(screen.getByText('Auto')).toBeTruthy();
  });

  it('applies --on / --disabled + className when controlled-checked', () => {
    const { container } = render(<Checkbox checked disabled className="extra" />);
    const root = container.querySelector('.mds-check');
    expect(root?.className).toContain('mds-check--on');
    expect(root?.className).toContain('mds-check--disabled');
    expect(root?.className).toContain('extra');
    expect(container.querySelector('.mds-check__box')?.textContent).toBe('✓');
  });

  it('toggles uncontrolled and forwards ref + onChange', () => {
    const ref = createRef<HTMLInputElement>();
    const onChange = vi.fn();
    const { container } = render(<Checkbox ref={ref} defaultChecked={false} onChange={onChange} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onChange).toHaveBeenCalledOnce();
    expect(container.querySelector('.mds-check')?.className).toContain('mds-check--on');
  });
});
