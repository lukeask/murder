/**
 * Select smoke test: renders options (strings + {value,label}), label/disabled + className map to
 * `.mds-select*` classes, the caret renders, and ref + onChange forward.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Select } from '../../src/components/ds/Select.js';

afterEach(cleanup);

describe('ds/Select', () => {
  it('renders options from strings and {value,label}, plus a caret', () => {
    const { container } = render(
      <Select options={['a', { value: 'b', label: 'Bee' }]} aria-label="pick" />,
    );
    const sel = screen.getByRole('combobox', { name: 'pick' });
    expect(sel.className).toContain('mds-select__el');
    expect(screen.getByRole('option', { name: 'a' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'Bee' })).toBeTruthy();
    expect(container.querySelector('.mds-select__caret svg')).not.toBeNull();
  });

  it('renders label + disabled and merges className', () => {
    const { container } = render(<Select label="Model" disabled className="extra" options={['x']} />);
    expect(screen.getByText('Model').className).toContain('mds-select__label');
    const root = container.querySelector('.mds-select');
    expect(root?.className).toContain('mds-select--disabled');
    expect(root?.className).toContain('extra');
    expect(screen.getByRole('combobox')).toHaveProperty('disabled', true);
  });

  it('forwards ref and fires onChange', () => {
    const ref = createRef<HTMLSelectElement>();
    const onChange = vi.fn();
    render(<Select ref={ref} onChange={onChange} options={['a', 'b']} />);
    expect(ref.current).toBeInstanceOf(HTMLSelectElement);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'b' } });
    expect(onChange).toHaveBeenCalledOnce();
  });
});
