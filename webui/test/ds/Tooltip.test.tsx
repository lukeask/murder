/**
 * Tooltip smoke test: renders the trigger plus a bubble carrying the label, and the placement prop
 * drives the modifier class (defaulting to top = no modifier).
 */

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Tooltip } from '../../src/components/ds/Tooltip.js';

afterEach(cleanup);

describe('ds/Tooltip', () => {
  it('renders the trigger and a labelled bubble', () => {
    const { container } = render(
      <Tooltip label="spawn a crow">
        <button type="button">+</button>
      </Tooltip>,
    );
    expect(screen.getByText('+')).toBeTruthy();
    const bubble = container.querySelector('.mds-tip__bubble') as HTMLElement;
    expect(bubble.getAttribute('role')).toBe('tooltip');
    expect(bubble.textContent).toBe('spawn a crow');
  });

  it('defaults to top placement (no modifier class)', () => {
    const { container } = render(<Tooltip label="x">t</Tooltip>);
    expect((container.querySelector('.mds-tip') as HTMLElement).className).not.toContain(
      'mds-tip--bottom',
    );
  });

  it('adds the bottom modifier class for placement="bottom"', () => {
    const { container } = render(
      <Tooltip label="x" placement="bottom">
        t
      </Tooltip>,
    );
    expect((container.querySelector('.mds-tip') as HTMLElement).className).toContain(
      'mds-tip--bottom',
    );
  });
});
