import { describe, expect, it } from 'vitest';
import { UsagePanel, type UsagePanelGroup } from '../../../src/components/panes/UsagePanel.js';
import { renderInkFixture, stripAnsiSgr } from '../../fixtures/pane_rendering/renderInkFixture.js';
import type { PaneFixture } from '../../fixtures/pane_rendering/types.js';

const wideShortUsageGroups: readonly UsagePanelGroup[] = [
  {
    harness: 'claude',
    steering: 'auto',
    gauges: [{ label: 'session', pct: 42, reset: '1h12m' }],
  },
  {
    harness: 'codex',
    steering: 'prefer',
    gauges: [{ label: 'weekly', pct: 65, reset: '4d3h' }],
  },
  {
    harness: 'cursor',
    steering: 'pause',
    gauges: [{ label: 'day', pct: 81, reset: '22m' }],
  },
];

const singleWideUsageGroup: readonly UsagePanelGroup[] = [
  {
    harness: 'claude',
    steering: 'auto',
    gauges: [
      { label: 'session', pct: 42, reset: '1h12m' },
      { label: 'weekly', pct: 65, reset: '4d3h' },
      { label: 'daily', pct: 81, reset: '22m' },
    ],
  },
];

const usageSpreadFixture: PaneFixture<readonly UsagePanelGroup[]> = {
  id: 'usage-panel-spread',
  description: 'Usage pane spread-layout regression fixture',
  sizes: [
    { id: 'wide-short', width: 60, height: 5 },
    { id: 'wide-five-inner-lines', width: 80, height: 7 },
    { id: 'single-group-short', width: 60, height: 5 },
  ],
  data: {
    wide: wideShortUsageGroups,
    single: singleWideUsageGroup,
  },
  render: ({ data, width, height, focused }) => (
    <UsagePanel width={width} height={height} focused={focused} groups={data} />
  ),
};

describe('UsagePanel — spread layout', () => {
  it('spreads provider groups horizontally when the pane is short and wide', async () => {
    const rendered = await renderInkFixture({
      fixture: usageSpreadFixture,
      dataId: 'wide',
      width: 60,
      height: 5,
      focused: true,
    });
    const lines = stripAnsiSgr(rendered.ansi).split('\n');

    expect(lines).toHaveLength(5);
    expect(lines[1]).toContain('claude');
    expect(lines[1]).toContain('codex');
    expect(lines[1]).toContain('cursor');
    expect(lines[2]).toContain('42%');
    expect(lines[2]).toContain('65%');
    expect(lines[2]).toContain('81%');
  });

  it('spreads provider groups horizontally at five drawable lines', async () => {
    const rendered = await renderInkFixture({
      fixture: usageSpreadFixture,
      dataId: 'wide',
      width: 80,
      height: 7,
      focused: true,
    });
    const lines = stripAnsiSgr(rendered.ansi).split('\n');

    expect(lines).toHaveLength(7);
    expect(lines[1]).toContain('claude');
    expect(lines[1]).toContain('codex');
    expect(lines[1]).toContain('cursor');
    expect(lines[2]).toContain('42%');
    expect(lines[2]).toContain('65%');
    expect(lines[2]).toContain('81%');
    expect(lines[3]).not.toContain('codex');
  });

  it('spreads gauges horizontally for one harness when the pane is short and wide', async () => {
    const rendered = await renderInkFixture({
      fixture: usageSpreadFixture,
      dataId: 'single',
      width: 60,
      height: 5,
      focused: true,
    });
    const lines = stripAnsiSgr(rendered.ansi).split('\n');
    const header = lines[1] ?? '';
    const body = lines[2] ?? '';

    expect(lines).toHaveLength(5);
    expect((header.match(/claude/g) ?? []).length).toBe(3);
    expect(body).toContain('42%');
    expect(body).toContain('65%');
    expect(body).toContain('81%');
    expect(lines.join('\n')).not.toContain('session');
    expect(lines.join('\n')).not.toContain('weekly');
    expect(lines.join('\n')).not.toContain('daily');
  });
});
