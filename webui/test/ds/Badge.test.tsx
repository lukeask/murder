/** Badge smoke test: tone + variant classes, dot slot, className merge. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Badge } from '../../src/components/ds/Badge.js';

afterEach(cleanup);

describe('ds/Badge', () => {
  it('defaults to neutral/soft and renders children', () => {
    const { container, getByText } = render(<Badge>idle</Badge>);
    const el = container.querySelector('.mds-badge');
    expect(el?.className).toContain('mds-badge--neutral');
    expect(el?.className).not.toContain('mds-badge--subtle');
    expect(el?.className).not.toContain('mds-badge--solid');
    expect(getByText('idle')).toBeTruthy();
  });

  it('applies tone + variant + dot + className', () => {
    const { container } = render(
      <Badge tone="running" variant="solid" dot className="extra">
        running
      </Badge>,
    );
    const el = container.querySelector('.mds-badge');
    expect(el?.className).toContain('mds-badge--running');
    expect(el?.className).toContain('mds-badge--solid');
    expect(el?.className).toContain('extra');
    expect(container.querySelector('.mds-badge__dot')).not.toBeNull();
  });

  it('subtle variant maps to the outline class', () => {
    const { container } = render(<Badge variant="subtle">x</Badge>);
    expect(container.querySelector('.mds-badge')?.className).toContain('mds-badge--subtle');
  });
});
