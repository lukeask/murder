import { describe, expect, it } from 'vitest';
import { CrowsPanel, type CrowsPanelRow } from '../../../src/components/panes/CrowsPanel.js';
import { renderInkFixture, stripAnsiSgr } from '../../fixtures/pane_rendering/renderInkFixture.js';
import type { PaneFixture } from '../../fixtures/pane_rendering/types.js';

const rows: readonly CrowsPanelRow[] = [
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

const crowsFixture: PaneFixture<readonly CrowsPanelRow[]> = {
  id: 'crows-panel-store-free',
  description: 'Store-free CrowsPanel regression fixture',
  sizes: [{ id: 'preferred', width: 54, height: 14 }],
  data: { mixed: rows },
  render: ({ data, width, height, focused }) => (
    <CrowsPanel width={width} height={height} focused={focused} rows={data} expanded />
  ),
};

describe('CrowsPanel — explicit pane contract', () => {
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
