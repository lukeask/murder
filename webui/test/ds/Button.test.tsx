/**
 * Button smoke test: renders, and the variant/size/block props + className merge map to the right
 * `.mds-btn*` classes. Pins the exemplar's class-composition contract for Phase B.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Button } from '../../src/components/ds/Button.js';

afterEach(cleanup);

describe('ds/Button', () => {
  it('renders children with the default secondary/md classes', () => {
    render(<Button>Spawn</Button>);
    const btn = screen.getByRole('button', { name: 'Spawn' });
    expect(btn.className).toContain('mds-btn');
    expect(btn.className).toContain('mds-btn--secondary');
    // md is the default and emits no size modifier.
    expect(btn.className).not.toContain('mds-btn--md');
    expect(btn).toHaveProperty('type', 'button');
  });

  it('applies variant, size, block and merges className', () => {
    render(
      <Button variant="primary" size="lg" block className="extra">
        Save
      </Button>,
    );
    const btn = screen.getByRole('button', { name: 'Save' });
    expect(btn.className).toContain('mds-btn--primary');
    expect(btn.className).toContain('mds-btn--lg');
    expect(btn.className).toContain('mds-btn--block');
    expect(btn.className).toContain('extra');
  });

  it('renders the keyHint chip and forwards ref + onClick', () => {
    const ref = createRef<HTMLButtonElement>();
    const onClick = vi.fn();
    render(
      <Button ref={ref} keyHint="C-s" onClick={onClick}>
        Save
      </Button>,
    );
    expect(screen.getByText('C-s').className).toContain('mds-btn__key');
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
