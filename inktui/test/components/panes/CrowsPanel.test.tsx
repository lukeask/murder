import { describe, expect, it } from 'vitest';
import { CrowsSurface, type CrowsSurfaceRow } from '../../../src/components/panes/CrowsSurface.js';
import { getTheme } from '../../../src/theme/themeStore.js';
import { renderInkFixture, stripAnsiSgr } from '../../fixtures/pane_rendering/renderInkFixture.js';
import type { PaneFixture } from '../../fixtures/pane_rendering/types.js';

const rows: readonly CrowsSurfaceRow[] = [
  {
    id: 'collab-1',
    group: 'Collaborator',
    name: 'collab',
    meta: 'claude · opus',
    working: true,
    starred: true,
    health: 'green',
  },
  {
    id: 'ticket-1',
    group: 'Ticket Crows',
    name: 'ticket-crow',
    meta: 'codex · gpt-5',
    working: false,
    starred: false,
    health: 'red',
  },
];

const crowsFixture: PaneFixture<readonly CrowsSurfaceRow[]> = {
  id: 'crows-surface-store-free',
  description: 'Store-free CrowsSurface regression fixture',
  sizes: [{ id: 'preferred', width: 54, height: 14 }],
  data: { mixed: rows },
  render: ({ data, width, height, focused }) => (
    <CrowsSurface
      width={width}
      height={height}
      focused={focused}
      theme={getTheme()}
      rows={data}
      expanded
    />
  ),
};

describe('CrowsSurface — explicit pane contract', () => {
  it('renders grouped crow rows and expanded metadata from explicit dimensions', async () => {
    const rendered = await renderInkFixture({
      fixture: crowsFixture,
      dataId: 'mixed',
      width: 54,
      height: 14,
      focused: true,
    });
    const frame = stripAnsiSgr(rendered.ansi);

    expect(frame).toContain('Crows');
    expect(frame).toContain('Collaborator');
    expect(frame).toContain('Ticket Crows');
    expect(frame).toContain('★');
    expect(frame).toContain('collab');
    expect(frame).toContain('ticket-crow');
    expect(frame).toContain('claude · opus');
    expect(frame).toContain('codex · gpt-5');
  });
});
