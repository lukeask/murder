/** Avatar smoke test: initial + size class + stable hash color, img fallback. */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Avatar } from '../../src/components/ds/Avatar.js';

afterEach(cleanup);

describe('ds/Avatar', () => {
  it('renders a lowercase initial and the size modifier', () => {
    const { container, getByText } = render(<Avatar name="Crow" size="lg" />);
    const el = container.querySelector('.mds-avatar');
    expect(el?.className).toContain('mds-avatar--lg');
    expect(getByText('c')).toBeTruthy();
    expect(el?.getAttribute('title')).toBe('Crow');
  });

  it('omits the size modifier for md and hashes a stable crow color', () => {
    const a = render(<Avatar name="alice" />).container.querySelector('.mds-avatar') as HTMLElement;
    expect(a.className).not.toContain('mds-avatar--md');
    const colorA = a.style.color;
    cleanup();
    const b = render(<Avatar name="alice" />).container.querySelector('.mds-avatar') as HTMLElement;
    expect(b.style.color).toBe(colorA);
    expect(colorA).toMatch(/var\(--crow-\d\)/);
  });

  it('renders an <img> when src is provided', () => {
    const { container } = render(<Avatar name="x" src="/pic.png" />);
    const img = container.querySelector('img');
    expect(img?.getAttribute('src')).toBe('/pic.png');
    expect(img?.getAttribute('alt')).toBe('x');
  });
});
