import { isValidElement, type ReactElement } from 'react';
import { describe, expect, it } from 'vitest';
import { crowsSurfaceRowsFromView } from '../../src/components/panes/CrowsController.js';
import { historySurfaceRowsFromView } from '../../src/components/panes/HistoryController.js';
import { ticketsSurfaceRowsFromView } from '../../src/components/panes/TicketsController.js';
import { treeSurfaceDataFromView } from '../../src/components/panes/TreeController.js';
import { usageSurfaceGroupsFromState } from '../../src/components/panes/UsageController.js';
import { renderPaneLayoutPlan, usagePaneSizing } from '../../src/layout/paneBridge.js';
import { computePaneLayout } from '../../src/layout/paneLayout.js';
import type { PaneRequest } from '../../src/layout/paneLayoutTypes.js';
import type { CrowsView } from '../../src/selectors/crowsSelectors.js';
import type { HistoryRowView } from '../../src/selectors/historySelectors.js';
import type { TicketRowView } from '../../src/selectors/ticketsSelectors.js';
import type { TransitCursor, TransitView } from '../../src/selectors/transitSelectors.js';
import type { AppStore } from '../../src/store/store.js';
import type { UsageRow, UsageState } from '../../src/store/usage/usageSlice.js';
import type { Theme } from '../../src/theme/buildTheme.js';
import { theme } from '../../src/theme.js';

function row(harness: string, windowKey: string): UsageRow {
  return {
    harness,
    windowKey,
    pct: 50,
    tUntilResetMinutes: 60,
    tPeriodMinutes: 300,
    steering: 'auto',
  };
}

function usage(rows: readonly UsageRow[]): { readonly usage: UsageState } {
  return {
    usage: {
      rows,
      status: 'ready',
      error: null,
    },
  };
}

function paneRequest(overrides: Partial<PaneRequest> = {}): PaneRequest {
  return {
    id: 'plans',
    kind: 'listPane',
    region: 'leftAligned',
    sizing: {
      min: { width: 25, height: 5 },
      preferred: { width: 25, height: 5 },
    },
    reapPriority: 40,
    orderKey: 0,
    source: { type: 'panel', panelId: 'plans' },
    ...overrides,
  };
}

function childrenOf(element: ReactElement): readonly unknown[] {
  const props = element.props as { readonly children?: unknown };
  const children = props.children;
  return Array.isArray(children) ? children : [children];
}

function propsOf(element: ReactElement): Record<string, unknown> {
  return element.props as Record<string, unknown>;
}

describe('usagePaneSizing', () => {
  it('uses 20 columns as the usage minimum width', () => {
    expect(usagePaneSizing(usage([])).min.width).toBe(20);
  });

  it('uses 5 rows as the usage minimum height so wide short layouts can mount', () => {
    const sizing = usagePaneSizing(
      usage([
        row('codex', '5h'),
        row('codex', 'weekly'),
        row('claude_code', '5h'),
        row('claude_code', 'weekly'),
        row('cursor', '5h'),
        row('cursor', 'weekly'),
      ]),
    );

    expect(sizing.min.height).toBe(5);
  });

  it('keeps preferred height at least as tall as the stacked usage form', () => {
    const rows = Array.from({ length: 18 }, (_, index) => row(`harness-${index}`, 'daily'));
    const sizing = usagePaneSizing(usage(rows));

    expect(sizing.preferred.height).toBe(2 + rows.length + rows.length);
  });

  it('maps live usage state into the new explicit usage pane props', () => {
    const groups = usageSurfaceGroupsFromState({
      rows: [
        {
          harness: 'codex',
          windowKey: 'current_session',
          pct: 42.4,
          tUntilResetMinutes: 72,
          tPeriodMinutes: 300,
          steering: 'prefer',
        },
        {
          harness: 'codex',
          windowKey: 'weekly',
          pct: 83.9,
          tUntilResetMinutes: 49 * 60,
          tPeriodMinutes: 7 * 24 * 60,
          steering: 'prefer',
        },
      ],
      status: 'ready',
      error: null,
    });

    expect(groups).toEqual([
      {
        harness: 'codex',
        steering: 'prefer',
        gauges: [
          { label: 'session', pct: 42, reset: '1h12m' },
          { label: 'weekly', pct: 84, reset: '2d1h' },
        ],
      },
    ]);
  });
});

describe('renderPaneLayoutPlan', () => {
  it('keeps wrapped portrait side-region allocations in separate rendered rows', () => {
    const plan = computePaneLayout({
      terminal: { width: 25, height: 17 },
      chrome: { topBar: 0, bottomBar: 0, chatInput: 0 },
      body: { width: 25, height: 17 },
      orientation: 'portrait',
      gap: 1,
      requests: ['notes', 'plans', 'reports'].map((id, index) =>
        paneRequest({
          id,
          orderKey: index,
          source: { type: 'panel', panelId: id as 'notes' | 'plans' | 'reports' },
        }),
      ),
    });
    const root = renderPaneLayoutPlan(plan, {
      state: {} as AppStore,
      chatIdentities: new Map(),
    });
    const [left] = childrenOf(root);

    expect(isValidElement(left)).toBe(true);
    if (!isValidElement(left)) {
      return;
    }

    expect(propsOf(left)['flexDirection']).toBe('column');
    const rows = childrenOf(left);
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(isValidElement(row)).toBe(true);
      if (isValidElement(row)) {
        const rowProps = propsOf(row);
        expect(rowProps['flexDirection']).toBe('row');
        expect(rowProps['width']).toBe(25);
        expect(rowProps['height']).toBe(5);
      }
    }
  });
});

describe('crowsSurfaceRowsFromView', () => {
  it('maps selector-owned CrowsView rows into the explicit pane contract', () => {
    const view: CrowsView = {
      status: 'ready',
      error: null,
      isEmpty: false,
      sections: [
        {
          group: 'collaborator',
          label: 'Collaborator',
          rows: [
            {
              agentId: 'collab-1',
              name: 'collab',
              favorited: true,
              status: 'running',
              working: true,
              harness: 'claude',
              model: 'opus',
              health: 'green',
            },
          ],
        },
        {
          group: 'ticket',
          label: 'Ticket Crows',
          rows: [
            {
              agentId: 'ticket-1',
              name: 'ticket-crow',
              favorited: false,
              status: 'blocked',
              working: false,
              harness: 'codex',
              model: 'gpt-5',
              health: 'red',
            },
          ],
        },
      ],
    };

    expect(crowsSurfaceRowsFromView(view)).toEqual([
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
    ]);
  });
});

describe('ticketsSurfaceRowsFromView', () => {
  it('drops selector-only fields while preserving explicit ticket pane cells', () => {
    const rows: TicketRowView[] = [
      {
        id: 'T-1',
        idCell: 'T-1',
        titleCell: 'Implement pane bridge',
        statusCell: '◕',
        statusTone: 'warning',
        lastUpdateCell: '2026-06-27 now',
        depsCell: 'ok',
        depsSatisfied: true,
        scheduleCell: 'now',
        harnessCell: 'codex',
        modelCell: 'gpt-5.5',
        planCell: 'panes',
        worktreeCell: 'paneswarm',
        rowParity: 1,
        depth: 2,
      },
    ];

    expect(ticketsSurfaceRowsFromView(rows)).toEqual([
      {
        id: 'T-1',
        idCell: 'T-1',
        titleCell: 'Implement pane bridge',
        statusCell: '◕',
        statusTone: 'warning',
        lastUpdateCell: '2026-06-27 now',
        depsCell: 'ok',
        depsSatisfied: true,
        scheduleCell: 'now',
        harnessCell: 'codex',
        modelCell: 'gpt-5.5',
        planCell: 'panes',
        worktreeCell: 'paneswarm',
      },
    ]);
  });
});

describe('historySurfaceRowsFromView', () => {
  it('maps history selector rows to display-only pane rows', () => {
    const rows: HistoryRowView[] = [
      {
        itemId: 'item-1',
        text: 'Continue the layout migration',
        target: 'codex',
        conversationId: 'conv-1',
        age: '4m',
        statusTag: 'OPEN',
        status: 'open',
        resumable: true,
      },
    ];

    expect(historySurfaceRowsFromView(rows)).toEqual([
      {
        id: 'item-1',
        age: '4m',
        target: 'codex',
        status: 'open',
        text: 'Continue the layout migration',
      },
    ]);
  });
});

describe('treeSurfaceDataFromView', () => {
  const transitView: TransitView = {
    lanes: [
      {
        branch: 'main',
        isMain: true,
        hint: 'm',
        headSha: 'm0',
        colorIndex: 0,
        tag: '▐ main ⌂ ▌',
        segments: [
          { text: '○━', color: 0 },
          { text: '◆', color: -2 },
        ],
        stationShas: ['m0'],
      },
      {
        branch: 'feature',
        isMain: false,
        hint: 'f',
        headSha: 'f0',
        colorIndex: 1,
        tag: '▐ feature ⌂ ▌',
        segments: [{ text: '╰━▶', color: 1 }],
        stationShas: ['f0'],
      },
    ],
    ruler: ' 1h  2d',
    railwayWidth: 12,
    tagColWidth: 14,
    mainIndex: 0,
    selected: {
      sha: 'f0',
      short: 'f0aaaa',
      branch: 'feature',
      subject: 'feature work',
      body: 'body',
      age: '1h',
    },
    infoLines: ['feature work', 'body'],
    status: 'ready',
    error: null,
    isEmpty: false,
  };

  const cursor: TransitCursor = { laneIndex: 1, sha: 'f0' };

  it('maps TransitView geometry into explicit TreeSurfaceData', () => {
    expect(treeSurfaceDataFromView(transitView, cursor, false, '', theme as Theme)).toMatchObject({
      ruler: ' 1h  2d',
      pending: false,
      status: 'ready',
      error: null,
      lanes: [
        { branch: 'main', rail: '○━◆', selected: false },
        { branch: 'feature', rail: '╰━▶', selected: true },
      ],
      info: ['f0aaaa · feature · 1h', 'feature work', 'body'],
    });
  });

  it('maps g-pending mode to hint overlay lines', () => {
    expect(treeSurfaceDataFromView(transitView, cursor, true, '20d', theme as Theme).info).toEqual([
      '[m] main  [f] feature',
      'type 5d/20m +⏎  · 20d',
    ]);
  });
});
