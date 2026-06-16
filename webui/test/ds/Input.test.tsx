/**
 * Input smoke test: renders the input, label/hint/leading slots, invalid + size + className map to
 * `.mds-input*` / `.mds-field*` classes, and ref + onChange forward.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Input } from '../../src/components/ds/Input.js';

afterEach(cleanup);

describe('ds/Input', () => {
  it('renders an input with default classes and no modifiers', () => {
    const { container } = render(<Input placeholder="search" />);
    const input = screen.getByPlaceholderText('search');
    expect(input.className).toContain('mds-input__el');
    const box = container.querySelector('.mds-input');
    expect(box).not.toBeNull();
    expect(box?.className).not.toContain('mds-input--lg');
    expect(box?.className).not.toContain('mds-input--invalid');
  });

  it('renders label + hint and applies size/invalid + className', () => {
    const { container } = render(
      <Input label="Name" hint="required" size="lg" invalid className="extra" leading={<span>L</span>} />,
    );
    expect(screen.getByText('Name').className).toContain('mds-field__label');
    const hint = screen.getByText('required');
    expect(hint.className).toContain('mds-field__hint--error');
    const box = container.querySelector('.mds-input');
    expect(box?.className).toContain('mds-input--lg');
    expect(box?.className).toContain('mds-input--invalid');
    expect(box?.className).toContain('extra');
    expect(container.querySelector('.mds-input__glyph')).not.toBeNull();
    expect(screen.getByRole('textbox')).toHaveProperty('ariaInvalid', 'true');
  });

  it('forwards ref and fires onChange', () => {
    const ref = createRef<HTMLInputElement>();
    const onChange = vi.fn();
    render(<Input ref={ref} onChange={onChange} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hi' } });
    expect(onChange).toHaveBeenCalledOnce();
  });
});
