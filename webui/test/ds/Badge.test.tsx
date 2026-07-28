/** Badge smoke test: tone classes, dot slot, className merge. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Badge } from '../../src/components/ds/Badge.js';

afterEach(cleanup);

describe('ds/Badge', () => {
  it('defaults to neutral and renders children', () => {
    const { container, getByText } = render(<Badge>idle</Badge>);
    const el = container.querySelector('.mds-badge');
    expect(el?.className).toContain('mds-badge--neutral');
    expect(getByText('idle')).toBeTruthy();
  });

  it('applies tone + dot + className', () => {
    const { container } = render(
      <Badge tone="running" dot className="extra">
        running
      </Badge>,
    );
    const el = container.querySelector('.mds-badge');
    expect(el?.className).toContain('mds-badge--running');
    expect(el?.className).toContain('extra');
    expect(container.querySelector('.mds-badge__dot')).not.toBeNull();
  });
});
