import { describe, expect, it } from 'vitest';
import { SCROLL_THUMB } from '../../../src/components/glyphs.js';
import { DocumentSurface } from '../../../src/components/panes/DocumentSurface.js';
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
});
