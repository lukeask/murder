/** useScrollEdges — more-above / more-below flags from scrollport geometry. */

import { cleanup, render } from '@testing-library/react';
import { useRef, useEffect, type ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  scrollEdgesClassName,
  useScrollEdges,
  type ScrollEdges,
} from '../src/useScrollEdges.js';

afterEach(cleanup);

function Probe({
  onEdges,
  scrollTop,
  clientHeight,
  scrollHeight,
}: {
  readonly onEdges: (edges: ScrollEdges) => void;
  readonly scrollTop: number;
  readonly clientHeight: number;
  readonly scrollHeight: number;
}): ReactNode {
  const ref = useRef<HTMLDivElement>(null);
  const edges = useScrollEdges(ref, scrollHeight);
  useEffect(() => {
    onEdges(edges);
  }, [edges, onEdges]);
  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight });
    Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight });
    el.scrollTop = scrollTop;
    el.dispatchEvent(new Event('scroll'));
  }, [scrollTop, clientHeight, scrollHeight]);
  return (
    <div ref={ref} className={scrollEdgesClassName(edges)}>
      content
    </div>
  );
}

describe('useScrollEdges', () => {
  it('scrollEdgesClassName includes both flags when clipped', () => {
    expect(scrollEdgesClassName({ moreAbove: false, moreBelow: false })).toBe('mds-scroll-edges');
    expect(scrollEdgesClassName({ moreAbove: true, moreBelow: true })).toContain(
      'mds-scroll-edges--more-above',
    );
    expect(scrollEdgesClassName({ moreAbove: true, moreBelow: true })).toContain(
      'mds-scroll-edges--more-below',
    );
  });

  it('reports moreBelow when content overflows below', async () => {
    let latest: ScrollEdges = { moreAbove: false, moreBelow: false };
    render(
      <Probe
        scrollTop={0}
        clientHeight={50}
        scrollHeight={200}
        onEdges={(e) => {
          latest = e;
        }}
      />,
    );
    await Promise.resolve();
    expect(latest.moreAbove).toBe(false);
    expect(latest.moreBelow).toBe(true);
  });

  it('reports moreAbove when scrolled down', async () => {
    let latest: ScrollEdges = { moreAbove: false, moreBelow: false };
    render(
      <Probe
        scrollTop={80}
        clientHeight={50}
        scrollHeight={200}
        onEdges={(e) => {
          latest = e;
        }}
      />,
    );
    await Promise.resolve();
    expect(latest.moreAbove).toBe(true);
    expect(latest.moreBelow).toBe(true);
  });
});
