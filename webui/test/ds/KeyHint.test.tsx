/** KeyHint smoke test: chord string vs array join, tone/boxed classes, desc slot. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { KeyHint } from '../../src/components/ds/KeyHint.js';

afterEach(cleanup);

describe('ds/KeyHint', () => {
  it('renders a string chord, green default (no tone modifier), no desc', () => {
    const { container, getByText } = render(<KeyHint chord="C-p" />);
    const el = container.querySelector('.mds-keyhint');
    expect(el?.className).not.toContain('mds-keyhint--yellow');
    expect(el?.className).not.toContain('mds-keyhint--muted');
    expect(getByText('C-p').className).toContain('mds-keyhint__chord');
    expect(container.querySelector('.mds-keyhint__desc')).toBeNull();
  });

  it('joins an array chord with "-" and renders desc', () => {
    const { getByText } = render(<KeyHint chord={['C', 'p']} desc="new plan" />);
    expect(getByText('C-p')).toBeTruthy();
    expect(getByText('new plan').className).toContain('mds-keyhint__desc');
  });

  it('applies tone + boxed modifiers', () => {
    const { container } = render(<KeyHint chord="x" tone="yellow" boxed />);
    const el = container.querySelector('.mds-keyhint');
    expect(el?.className).toContain('mds-keyhint--yellow');
    expect(el?.className).toContain('mds-keyhint--boxed');
  });
});
