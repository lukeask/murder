/** Tag smoke test: tone class and dot. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Tag } from '../../src/components/ds/Tag.js';

afterEach(cleanup);

describe('ds/Tag', () => {
  it('defaults to neutral (no tone modifier) and renders children', () => {
    const { container, getByText } = render(<Tag>gpt-5</Tag>);
    const el = container.querySelector('.mds-tag');
    expect(el?.className).not.toContain('mds-tag--accent');
    expect(el?.className).not.toContain('mds-tag--brand');
    expect(getByText('gpt-5')).toBeTruthy();
  });

  it('applies tone + dot', () => {
    const { container } = render(
      <Tag tone="accent" dot>
        x
      </Tag>,
    );
    expect(container.querySelector('.mds-tag')?.className).toContain('mds-tag--accent');
    expect(container.querySelector('.mds-tag__dot')).not.toBeNull();
  });
});
