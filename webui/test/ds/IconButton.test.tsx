/**
 * IconButton smoke test: renders, size/active/bordered + className map to `.mds-iconbtn*` classes,
 * label drives aria-label/title, and ref + onClick forward.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { IconButton } from '../../src/components/ds/IconButton.js';

afterEach(cleanup);

describe('ds/IconButton', () => {
  it('renders with default sm classes and label as aria-label/title', () => {
    render(<IconButton label="Settings">x</IconButton>);
    const btn = screen.getByRole('button', { name: 'Settings' });
    expect(btn.className).toContain('mds-iconbtn');
    expect(btn.className).not.toContain('mds-iconbtn--sm');
    expect(btn).toHaveProperty('title', 'Settings');
    expect(btn).toHaveProperty('type', 'button');
  });

  it('applies size, active, bordered and merges className', () => {
    render(
      <IconButton label="Pin" size="lg" active bordered className="extra">
        x
      </IconButton>,
    );
    const btn = screen.getByRole('button', { name: 'Pin' });
    expect(btn.className).toContain('mds-iconbtn--lg');
    expect(btn.className).toContain('mds-iconbtn--active');
    expect(btn.className).toContain('mds-iconbtn--bordered');
    expect(btn.className).toContain('extra');
  });

  it('forwards ref and fires onClick', () => {
    const ref = createRef<HTMLButtonElement>();
    const onClick = vi.fn();
    render(
      <IconButton ref={ref} label="Go" onClick={onClick}>
        x
      </IconButton>,
    );
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(onClick).toHaveBeenCalledOnce();
  });
});
