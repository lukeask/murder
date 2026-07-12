import { describe, expect, it } from 'vitest';
import { SCROLL_THUMB } from '../../../src/components/glyphs.js';
import {
  DocumentSurface,
  documentPhysicalRows,
} from '../../../src/components/panes/DocumentSurface.js';
import { renderInkFixture, stripAnsiSgr } from '../../fixtures/pane_rendering/renderInkFixture.js';
import type { PaneFixture } from '../../fixtures/pane_rendering/types.js';

const lines = Array.from({ length: 10 }, (_, index) => `doc-line-${index + 1}`);

const fixture: PaneFixture<readonly string[]> = {
  id: 'document-surface-scroll',
  description: 'DocumentSurface scroll-window fixture',
  sizes: [{ id: 'preferred', width: 36, height: 6 }],
  data: { long: lines },
  render: ({ data, width, height, focused }) => (
    <DocumentSurface
      width={width}
      height={height}
      focused={focused}
      title=".murder/plans/scroll.md"
      lines={data}
      scroll={3}
    />
  ),
};

describe('DocumentSurface', () => {
  it('renders the requested document window with a scrollbar thumb on the right border', async () => {
    const rendered = await renderInkFixture({
      fixture,
      dataId: 'long',
      width: 36,
      height: 6,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);

    expect(frame).toContain('scroll');
    expect(frame).toContain('doc-line-4');
    expect(frame).toContain('doc-line-7');
    expect(frame).not.toContain('doc-line-3');
    expect(frame).not.toContain('doc-line-8');
    expect(frame).toContain(SCROLL_THUMB);
  });

  it('pre-wraps long logical lines into physical rows before windowing', async () => {
    const longUrl = `https://example.com/${'x'.repeat(80)}`;
    const wrapFixture: PaneFixture<readonly string[]> = {
      id: 'document-surface-wrap',
      description: 'DocumentSurface physical-row wrap fixture',
      sizes: [{ id: 'preferred', width: 36, height: 6 }],
      data: { url: [longUrl, 'second'] },
      render: ({ data, width, height, focused }) => (
        <DocumentSurface
          width={width}
          height={height}
          focused={focused}
          title=".murder/plans/wrap.md"
          lines={data}
          scroll={0}
        />
      ),
    };
    const physical = documentPhysicalRows([longUrl, 'second'], 36, 6);
    expect(physical.length).toBeGreaterThan(2);

    const rendered = await renderInkFixture({
      fixture: wrapFixture,
      dataId: 'url',
      width: 36,
      height: 6,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);
    expect(frame).toContain('https://example.com/');
    expect(frame).not.toContain('\r');
  });

  it('wraps (does not truncate) in compact and minimal layouts', () => {
    const long = 'abcdefghijklmnopqrstuvwxyz';
    // compact: innerW < 16 → width 14 (inner 12), height enough for compact not micro
    const compactRows = documentPhysicalRows([long], 14, 6);
    expect(compactRows.length).toBeGreaterThan(1);
    expect(compactRows.every((row) => row.length <= 12)).toBe(true);
    expect(compactRows.join('')).toBe(long);

    // minimal: innerH < 2 → height 3; keep width above micro threshold but force wrap
    const minimalLong = 'x'.repeat(80);
    const minimalRows = documentPhysicalRows([minimalLong], 36, 3);
    expect(minimalRows.length).toBeGreaterThan(1);
    expect(minimalRows.join('')).toBe(minimalLong);
  });

  it('strips control characters from document body text', async () => {
    const dirtyFixture: PaneFixture<readonly string[]> = {
      id: 'document-surface-controls',
      description: 'DocumentSurface control sanitization fixture',
      sizes: [{ id: 'preferred', width: 36, height: 6 }],
      data: { dirty: ['safe\r\u001B[2Aleak', 'ok'] },
      render: ({ data, width, height, focused }) => (
        <DocumentSurface
          width={width}
          height={height}
          focused={focused}
          title=".murder/plans/ctrl.md"
          lines={data}
          scroll={0}
        />
      ),
    };
    const rendered = await renderInkFixture({
      fixture: dirtyFixture,
      dataId: 'dirty',
      width: 36,
      height: 6,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);
    expect(frame).toContain('safe');
    expect(frame).toContain('leak');
    expect(frame).not.toContain('\u001B');
    expect(frame).not.toContain('\r');
  });
});
